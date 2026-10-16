import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { MessageItem } from '$lib/api/types';
import type { LiveEvent, SseMessage } from '$lib/live/sse';
import { deferred } from '$lib/test/deferred';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { CHAT_PAGE, GameChat } from './store.svelte';

const GAME = 227859379;

const item = (id: number, msg_id: number, patch: Partial<MessageItem> = {}): MessageItem => ({
	type: 'message',
	id,
	at: '2026-10-17T10:00:01Z',
	chat_id: GAME,
	msg_id,
	revision: 0,
	kind: 'new',
	date: '2026-10-17T10:00:00Z',
	outgoing: false,
	recovered: false,
	text: `#${msg_id}`,
	markup: null,
	events: [],
	source: null,
	...patch
});

const live = (msg_id: number, patch: Partial<SseMessage> = {}): LiveEvent => ({
	type: 'message',
	id: 'e:1',
	data: {
		journal_id: 900 + msg_id,
		chat_id: GAME,
		msg_id,
		revision: 0,
		kind: 'new',
		date: '2026-10-17T10:05:00Z',
		outgoing: false,
		text: `live ${msg_id}`,
		events: [],
		markup: null,
		...patch
	}
});

function setup(pages: Record<string, { items: MessageItem[]; next_cursor: string | null }>, settings: unknown = fixture('settings')) {
	const fetch = mockFetch((c: Call) => {
		if (c.url === '/api/v1/accounts/1/settings') return json(settings);
		if (c.url.startsWith('/api/v1/accounts/1/journal?')) {
			const cursor = new URL(c.url, 'http://x').searchParams.get('cursor') ?? '';
			return json(pages[cursor] ?? { items: [], next_cursor: null });
		}
		return json({ detail: 'Not Found' }, 404);
	});
	const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	return { fetch, chat: new GameChat(api) };
}

const ids = (chat: GameChat) => chat.messages.map((m) => m.msg_id);

describe('чат игры: история и поток', () => {
	it('чат игры — из настроек; первая страница — только его сообщения, новые снизу', async () => {
		const { fetch, chat } = setup({ '': { items: [item(3, 12), item(2, 11), item(1, 10)], next_cursor: 'c1' } });
		await chat.start();
		expect(chat.chatId).toBe(GAME);
		const q = new URL(fetch.calls.find((c) => c.url.includes('/journal'))!.url, 'http://x').searchParams;
		expect(Object.fromEntries(q)).toEqual({ types: 'message', chat_id: String(GAME), limit: String(CHAT_PAGE) });
		expect(ids(chat)).toEqual([10, 11, 12]);
		expect(chat.hasOlder).toBe(true);
		expect(chat.loaded).toBe(true);
	});

	it('без своего значения в настройках — чат по умолчанию', async () => {
		const settings = fixture<{ values: Record<string, unknown>; defaults: Record<string, unknown> }>('settings');
		const { chat } = setup({}, { ...settings, values: { ...settings.values, chats: {} } });
		await chat.start();
		expect(chat.chatId).toBe(GAME);
	});

	it('раньше: страница по курсору, порядок сохраняется, правки старших страниц не перетирают новые', async () => {
		const { chat } = setup({
			'': { items: [item(5, 21), item(4, 20, { kind: 'edit', revision: 50, text: 'правка' })], next_cursor: 'c1' },
			c1: { items: [item(3, 20, { text: 'исходное' }), item(2, 19), item(1, 18)], next_cursor: null }
		});
		await chat.start();
		await chat.loadOlder();
		expect(ids(chat)).toEqual([18, 19, 20, 21]);
		expect(chat.messages.find((m) => m.msg_id === 20)?.text).toBe('правка');
		expect(chat.hasOlder).toBe(false);
		await chat.loadOlder();
		expect(ids(chat)).toEqual([18, 19, 20, 21]);
	});

	it('поток: новое сообщение чата игры, его правка; чужой чат — мимо', async () => {
		const { chat } = setup({ '': { items: [item(1, 10)], next_cursor: null } });
		await chat.start();
		chat.onEvent(live(11, { outgoing: true, source: 'planner', text: '/inv' }));
		chat.onEvent(live(12, { chat_id: -100 }));
		expect(ids(chat)).toEqual([10, 11]);
		expect(chat.messages[1]).toMatchObject({ outgoing: true, source: 'planner' });
		chat.onEvent(live(10, { kind: 'edit', revision: 77, text: 'изменено' }));
		expect(chat.messages[0]).toMatchObject({ text: 'изменено', edited: true });
		expect(chat.newest).toBe(11);
	});

	it('кадры во время загрузки истории не теряются и не откатываются', async () => {
		const page = deferred<Response>();
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/accounts/1/settings') return json(fixture('settings'));
			return page.promise;
		});
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const chat = new GameChat(api);
		const started = chat.start();
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.url.includes('/journal'))).toBe(true));
		chat.onEvent(live(11));
		chat.onEvent(live(10, { kind: 'edit', revision: 9, text: 'свежая правка' }));
		page.resolve(json({ items: [item(1, 10, { text: 'старое' })], next_cursor: null }));
		await started;
		expect(ids(chat)).toEqual([10, 11]);
		expect(chat.messages[0]?.text).toBe('свежая правка');
	});

	it('reset потока — история заново', async () => {
		const pages = { '': { items: [item(1, 10)], next_cursor: null } };
		const { fetch, chat } = setup(pages);
		await chat.start();
		pages[''] = { items: [item(2, 11), item(1, 10)], next_cursor: null };
		chat.onEvent({ type: 'reset', id: 'e:2', data: { reason: 'evicted' } });
		await chat.idle();
		expect(ids(chat)).toEqual([10, 11]);
		expect(fetch.calls.filter((c) => c.url.includes('/journal'))).toHaveLength(2);
	});

	it('системная строка — после последнего сообщения на момент появления', async () => {
		const { chat } = setup({ '': { items: [item(1, 10)], next_cursor: null } });
		await chat.start();
		chat.note('Отклонено: пауза', 'error');
		chat.onEvent(live(11));
		expect(chat.entries.map((e) => (e.type === 'message' ? e.message.msg_id : e.note.text))).toEqual([
			10,
			'Отклонено: пауза',
			11
		]);
	});

	it('ошибка загрузки — видна, повтор читает заново', async () => {
		let fail = true;
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/accounts/1/settings') return fail ? json({ detail: 'boom' }, 500) : json(fixture('settings'));
			return json({ items: [item(1, 10)], next_cursor: null });
		});
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const chat = new GameChat(api);
		await chat.start();
		expect(chat.error).not.toBeNull();
		fail = false;
		await chat.start();
		expect(chat.error).toBeNull();
		expect(ids(chat)).toEqual([10]);
	});
});
