import { describe, expect, it } from 'vitest';
import type { MessageItem } from '$lib/api/types';
import type { SseMessage } from '$lib/live/sse';
import { fromItem, fromLive, inlineRows, merge, replyKeyboard, safeUrl, upsert, type ChatMessage } from './chat';

const item = (patch: Partial<MessageItem>): MessageItem => ({
	type: 'message',
	id: 1,
	at: '2026-10-17T10:00:01Z',
	chat_id: 227859379,
	msg_id: 100,
	revision: 0,
	kind: 'new',
	date: '2026-10-17T10:00:00Z',
	outgoing: false,
	recovered: false,
	text: 'текст',
	markup: null,
	events: [],
	source: null,
	...patch
});

const msg = (msg_id: number, patch: Partial<ChatMessage> = {}): ChatMessage => ({
	chat_id: 227859379,
	msg_id,
	revision: 0,
	edited: false,
	date: '2026-10-17T10:00:00Z',
	outgoing: false,
	text: `#${msg_id}`,
	markup: null,
	source: null,
	...patch
});

describe('сообщения чата игры', () => {
	it('элемент журнала и кадр потока — в одну форму; правка помечена', () => {
		expect(fromItem(item({ kind: 'edit', revision: 1790535909, source: undefined }))).toEqual({
			chat_id: 227859379,
			msg_id: 100,
			revision: 1790535909,
			edited: true,
			date: '2026-10-17T10:00:00Z',
			outgoing: false,
			text: 'текст',
			markup: null,
			source: null
		});
		const live: SseMessage = {
			journal_id: 5,
			chat_id: 1,
			msg_id: 7,
			revision: 0,
			kind: 'new',
			date: '2026-10-17T10:00:00Z',
			outgoing: true,
			text: '/inv',
			events: [],
			markup: null,
			source: 'manual'
		};
		expect(fromLive(live)).toMatchObject({ msg_id: 7, edited: false, outgoing: true, source: 'manual' });
		expect(fromLive({ ...live, source: undefined }).source).toBeNull();
	});

	it('правки сворачиваются по msg_id: остаётся старшая ревизия, порядок — по msg_id', () => {
		const items = [
			item({ id: 4, msg_id: 102, revision: 0, text: 'новое' }),
			item({ id: 3, msg_id: 100, revision: 1790535909, kind: 'edit', text: 'правка 2' }),
			item({ id: 2, msg_id: 101, revision: 0, outgoing: true, source: 'planner', text: '/inv' }),
			item({ id: 1, msg_id: 100, revision: 1790535904, kind: 'edit', text: 'правка 1' })
		];
		const list = merge([], items.map(fromItem));
		expect(list.map((m) => [m.msg_id, m.text])).toEqual([
			[100, 'правка 2'],
			[101, '/inv'],
			[102, 'новое']
		]);
	});

	it('upsert: новое — на своё место, старшая ревизия заменяет, младшая — нет', () => {
		let list = [msg(10), msg(12)];
		list = upsert(list, msg(11));
		expect(list.map((m) => m.msg_id)).toEqual([10, 11, 12]);
		list = upsert(list, msg(11, { revision: 5, edited: true, text: 'изм' }));
		expect(list.find((m) => m.msg_id === 11)?.text).toBe('изм');
		list = upsert(list, msg(11, { revision: 3, text: 'старое' }));
		expect(list.find((m) => m.msg_id === 11)?.text).toBe('изм');
		expect(list).toHaveLength(3);
	});

	it('отправитель своего сообщения не теряется на правке без него', () => {
		const list = upsert([msg(1, { outgoing: true, source: 'planner' })], msg(1, { outgoing: true, revision: 2 }));
		expect(list[0]?.source).toBe('planner');
	});

	it('кнопки — по рядам и колонкам', () => {
		const rows = inlineRows({
			inline: [
				['B', 1, 0, 'b', null, null],
				['A2', 0, 1, 'a2', null, null],
				['A1', 0, 0, 'a1', null, null]
			]
		});
		expect(rows.map((r) => r.map((b) => b[0]))).toEqual([['A1', 'A2'], ['B']]);
		expect(inlineRows(null)).toEqual([]);
	});

	it('клавиатура — у последнего входящего сообщения с ней', () => {
		const older = msg(1, { markup: { reply: [['🎒Рюкзак']] } });
		const newer = msg(2, { markup: { reply: [['⛏Работа', '📚Учёба']] } });
		const mine = msg(3, { outgoing: true, markup: { reply: [['x']] } });
		expect(replyKeyboard([older, newer, mine, msg(4)])).toEqual([['⛏Работа', '📚Учёба']]);
		expect(replyKeyboard([msg(1)])).toBeNull();
	});

	it('ссылка кнопки — только http(s)', () => {
		expect(safeUrl('https://t.me/x')).toBe('https://t.me/x');
		expect(safeUrl('http://example.com')).toBe('http://example.com');
		expect(safeUrl('javascript:alert(1)')).toBeNull();
		expect(safeUrl('tg://resolve?domain=x')).toBeNull();
		expect(safeUrl(null)).toBeNull();
	});
});
