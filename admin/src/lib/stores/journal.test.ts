import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { JournalPage } from '$lib/api/types';
import { decodeEvent, type LiveEvent } from '$lib/live/sse';
import { deferred, flush, type Deferred } from '$lib/test/deferred';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { EMPTY_FILTER, feedQuery, JournalFeed, keyOf, LIVE_KEPT } from './journal.svelte';

const page = fixture<JournalPage>('journal_page');

function feed(pages: JournalPage[] = [page]) {
	const fetch = mockFetch(() => json(pages.shift() ?? { items: [], next_cursor: null }));
	const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	return { f: new JournalFeed(api), fetch };
}

const ev = (type: string, data: unknown): LiveEvent => decodeEvent(type, JSON.stringify(data), 'e:1')!;

describe('параметры ленты', () => {
	it('статус и источник — только действия; даты — сутки МСК', () => {
		expect(feedQuery({ ...EMPTY_FILTER, type: 'decision', status: 'refused' }, null)).toEqual({
			types: 'action',
			status: 'refused',
			limit: 50
		});
		expect(feedQuery({ ...EMPTY_FILTER, since: '2026-09-27', until: '2026-09-27' }, 'CUR')).toEqual({
			since: '2026-09-26T21:00:00.000Z',
			until: '2026-09-27T21:00:00.000Z',
			cursor: 'CUR',
			limit: 50
		});
	});
});

describe('лента журнала', () => {
	it('первая страница с прода и подгрузка по курсору без повторов', async () => {
		const second: JournalPage = { items: [page.items[59]!, { ...page.items[0]!, id: 1 } as never], next_cursor: null };
		const { f, fetch } = feed([page, second]);
		await f.reload();
		expect(f.items).toHaveLength(60);
		expect(f.cursor).toBe(page.next_cursor);
		await f.more();
		expect(fetch.calls[1]?.url).toContain(`cursor=${page.next_cursor}`);
		expect(f.items).toHaveLength(61);
		expect(f.done).toBe(true);
	});

	it('живых строк сверху — не больше LIVE_KEPT: старые отбрасываются, страницы и курсор не трогаются', async () => {
		const { f } = feed();
		await f.reload();
		const decision = (id: number) => ev('decision', { id, at: '2026-09-27T20:30:00Z', kind: 'wait', scenario: null, reason: 'busy', until: null });
		for (let id = 1000; id < 1000 + LIVE_KEPT + 20; id++) f.onEvent(decision(id));
		expect(LIVE_KEPT).toBe(500);
		expect(f.items).toHaveLength(LIVE_KEPT + page.items.length);
		expect(keyOf(f.items[0]!)).toBe(`decision:${1000 + LIVE_KEPT + 19}`);
		expect(keyOf(f.items[LIVE_KEPT - 1]!)).toBe('decision:1020');
		expect(f.items.slice(LIVE_KEPT).map(keyOf)).toEqual(page.items.map(keyOf));
		expect(f.cursor).toBe(page.next_cursor);
		// Обновление действия со страницы по-прежнему меняет его строку.
		f.onEvent(ev('action', { id: 474, status: 'refused', reason: 'busy' }));
		const row = f.items.find((i) => keyOf(i) === 'action:474');
		expect(row?.type === 'action' && row.status).toBe('refused');
		// Повтор кадра строки, которая ещё в ленте, её не дублирует.
		f.onEvent(decision(1000 + LIVE_KEPT + 19));
		expect(f.items).toHaveLength(LIVE_KEPT + page.items.length);
	});

	it('живое сверху, обновление действия меняет строку', async () => {
		const { f } = feed();
		await f.reload();
		f.onEvent(ev('decision', { id: 345, at: '2026-09-27T20:30:00Z', kind: 'act', scenario: 'book', reason: 'ready', until: null }));
		f.onEvent(
			ev('action', { id: 480, status: 'intent', reason: '', source: 'scenario', kind: 'send', chat_id: 1, text: '/read_exp', data: null, command_class: 'action' })
		);
		expect(f.items.slice(0, 2).map(keyOf)).toEqual(['action:480', 'decision:345']);
		f.onEvent(ev('action', { id: 474, status: 'refused', reason: 'busy' }));
		const row = f.items.find((i) => keyOf(i) === 'action:474');
		expect(row?.type === 'action' && [row.status, row.reason]).toEqual(['refused', 'busy']);
		// Повтор кадра не дублирует строку.
		f.onEvent(ev('decision', { id: 345, at: '2026-09-27T20:30:00Z', kind: 'act', scenario: 'book', reason: 'ready', until: null }));
		expect(f.items.filter((i) => keyOf(i) === 'decision:345')).toHaveLength(1);
	});

	it('фильтр и выключенный live', async () => {
		const { f } = feed([page, page]);
		await f.reload();
		f.filter = { ...EMPTY_FILTER, type: 'message' };
		f.onEvent(ev('decision', { id: 346, at: 'x', kind: 'wait', scenario: null, reason: 'busy', until: null }));
		expect(f.items[0]?.id).not.toBe(346);
		f.live = false;
		f.onEvent(
			ev('message', { journal_id: 900, chat_id: 1, msg_id: 2, revision: 0, kind: 'new', date: 'x', outgoing: false, text: 'hi', events: [] })
		);
		expect(f.missed).toBe(1);
		expect(f.items.some((i) => i.id === 900)).toBe(false);
	});

	it('reset перечитывает первую страницу', async () => {
		const { f, fetch } = feed([page, page]);
		await f.reload();
		f.onEvent(ev('reset', { reason: 'epoch' }));
		await new Promise((r) => setTimeout(r, 0));
		expect(fetch.calls).toHaveLength(2);
	});
});


const created = (id: number, status = 'intent', source = 'scenario') =>
	ev('action', { id, status, reason: '', source, kind: 'send', chat_id: 1, text: '/job', data: null, command_class: 'action' });
const actionOut = (id: number, status: string, created_at = '2026-09-27T20:30:00Z') => ({
	id, created_at, source: 'scenario', kind: 'send', chat_id: 1, payload: { text: '/job', data: null },
	command_class: 'action', status, reason: 'busy', attempts: 1, answer: null, match_detail: 'busy',
	sent_at: created_at, finished_at: created_at, reconciled_at: null, idempotency_key: null, scenario_run_id: 7
});

/** Ответы по пути: /journal — очередь отложенных страниц, /actions/{id} — из таблицы. */
function routed(actions: Record<number, object> = {}) {
	const pages: Deferred<JournalPage>[] = [];
	const fetch = mockFetch(async (c: Call) => {
		const m = /^\/api\/v1\/accounts\/1\/actions\/(\d+)$/.exec(c.url);
		if (m) return actions[Number(m[1])] ? json(actions[Number(m[1])]) : json({ detail: 'action not found' }, 404);
		const d = deferred<JournalPage>();
		pages.push(d);
		return json(await d.promise);
	});
	const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	return { f: new JournalFeed(api), pages, fetch };
}

describe('связи запуска в ленте', () => {
	it('живое действие — со своим запуском, кадр начала запуска связывает с ним решение', async () => {
		const { f } = feed([{ items: [], next_cursor: null }]);
		await f.reload();
		f.onEvent(ev('decision', { id: 500, at: '2026-09-27T20:30:00Z', kind: 'act', scenario: 'sleep', reason: 'sleep_deadline', until: null }));
		expect(f.items[0]).toMatchObject({ type: 'decision', id: 500, run_id: null });
		f.onEvent(ev('scenario_run', { id: 77, scenario: 'sleep', status: 'running', reason: '', decision_id: 500 }));
		expect(f.items[0]).toMatchObject({ type: 'decision', id: 500, run_id: 77 });
		f.onEvent(ev('action', {
			id: 900, status: 'intent', reason: '', source: 'scenario', kind: 'send', chat_id: 1, text: '🛌Спать', data: null,
			command_class: 'nav', scenario_run_id: 77
		}));
		expect(f.items[0]).toMatchObject({ type: 'action', id: 900, run_id: 77 });
		// Кадры без решения (ручной запуск, конец запуска) ничего не связывают.
		f.onEvent(ev('scenario_run', { id: 78, scenario: 'card', status: 'queued', reason: '', decision_id: null }));
		f.onEvent(ev('scenario_run', { id: 77, scenario: null, status: 'done', reason: 'fell_asleep' }));
		expect(f.items.find((i) => i.type === 'decision')).toMatchObject({ run_id: 77 });
	});
});

describe('гонки и фильтры ленты', () => {
	it('кадры во время загрузки первой страницы не теряются и не дублируются', async () => {
		const { f, pages } = routed();
		const loading = f.reload();
		await flush();
		f.onEvent(ev('decision', { id: 345, at: '2026-09-27T20:30:00Z', kind: 'act', scenario: 'book', reason: 'ready', until: null }));
		f.onEvent(created(480));
		// Этот кадр уже есть в странице — дубля не будет.
		f.onEvent(ev('decision', { id: 344, at: '2026-09-27T19:47:42Z', kind: 'wait', scenario: null, reason: 'busy', until: null }));
		// Обновление строки из будущей страницы применяется после её прихода.
		f.onEvent(ev('action', { id: 474, status: 'refused', reason: 'busy' }));
		pages[0]!.resolve(page);
		await loading;
		expect(f.items.slice(0, 3).map(keyOf)).toEqual(['action:480', 'decision:345', 'decision:344']);
		expect(f.items.filter((i) => keyOf(i) === 'decision:344')).toHaveLength(1);
		expect(f.items).toHaveLength(62);
		const row = f.items.find((i) => keyOf(i) === 'action:474');
		expect(row?.type === 'action' && row.status).toBe('refused');
	});

	it('поздняя первая страница прежнего фильтра не затирает новую', async () => {
		const { f, pages } = routed();
		const first = f.reload();
		await flush();
		f.setFilter({ type: 'decision' });
		await flush();
		pages[1]!.resolve({ items: [page.items[0]!], next_cursor: null });
		await flush();
		pages[0]!.resolve(page);
		await first;
		expect(f.items.map(keyOf)).toEqual(['decision:344']);
	});

	it('действие стало ошибкой после создания — дозапрос и строка в отфильтрованной ленте', async () => {
		const { f, pages, fetch } = routed({ 480: actionOut(480, 'refused') });
		f.filter = { ...EMPTY_FILTER, type: 'action', status: 'refused' };
		const loading = f.reload();
		await flush();
		pages[0]!.resolve({ items: [], next_cursor: null });
		await loading;
		f.onEvent(created(480));
		expect(f.items).toEqual([]);
		f.onEvent(ev('action', { id: 480, status: 'sent', reason: '' }));
		f.onEvent(ev('action', { id: 480, status: 'refused', reason: 'busy' }));
		// Повторный кадр не шлёт второй запрос.
		f.onEvent(ev('action', { id: 480, status: 'refused', reason: 'busy' }));
		await vi.waitFor(() => expect(f.items.map(keyOf)).toEqual(['action:480']));
		expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/actions/480')).toHaveLength(1);
		expect(f.items[0]).toMatchObject({ status: 'refused', reason: 'busy', at: '2026-09-27T20:30:00Z', text: '/job' });
	});

	it('дозапрошенное действие не из фильтра источника или дат — не добавляется', async () => {
		const { f, pages, fetch } = routed({ 481: { ...actionOut(481, 'refused'), source: 'manual' }, 482: actionOut(482, 'refused', '2026-09-20T10:00:00Z') });
		f.filter = { ...EMPTY_FILTER, type: 'action', source: 'scenario', since: '2026-09-27' };
		const loading = f.reload();
		await flush();
		pages[0]!.resolve({ items: [], next_cursor: null });
		await loading;
		f.onEvent(ev('action', { id: 481, status: 'refused', reason: 'busy' }));
		f.onEvent(ev('action', { id: 482, status: 'refused', reason: 'busy' }));
		await vi.waitFor(() => expect(fetch.calls.filter((c) => c.url.startsWith('/api/v1/accounts/1/actions/'))).toHaveLength(2));
		for (let i = 0; i < 5; i++) await flush();
		expect(f.items).toEqual([]);
		// Кадр создания показал чужой источник — обновления без дозапроса.
		f.onEvent(created(483, 'intent', 'manual'));
		f.onEvent(ev('action', { id: 483, status: 'refused', reason: 'busy' }));
		await flush();
		expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/actions/483')).toHaveLength(0);
	});

	it('живые записи — по датам фильтра', async () => {
		const { f, pages } = routed();
		f.filter = { ...EMPTY_FILTER, since: '2026-09-27', until: '2026-09-27' };
		const loading = f.reload();
		await flush();
		pages[0]!.resolve({ items: [], next_cursor: null });
		await loading;
		const message = (id: number) =>
			ev('message', { journal_id: id, chat_id: 1, msg_id: id, revision: 0, kind: 'new', date: 'x', outgoing: false, text: 'hi', events: [] });
		// Момент живого сообщения — момент прихода.
		f.onEvent(message(1), new Date('2026-09-27T20:00:00Z'));
		f.onEvent(message(2), new Date('2026-09-28T10:00:00Z'));
		f.onEvent(message(3), new Date('2026-09-26T20:59:59Z'));
		// Момент решения — его собственный.
		f.onEvent(ev('decision', { id: 4, at: '2026-09-26T10:00:00Z', kind: 'wait', scenario: null, reason: 'busy', until: null }));
		f.onEvent(ev('decision', { id: 5, at: '2026-09-27T12:00:00Z', kind: 'wait', scenario: null, reason: 'busy', until: null }));
		expect(f.items.map(keyOf)).toEqual(['decision:5', 'message:1']);
	});
});
