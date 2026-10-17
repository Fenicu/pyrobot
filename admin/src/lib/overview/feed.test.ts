import { describe, expect, it } from 'vitest';
import type { FeedItem, OverviewFeed } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { feedIcon, feedText, filtersKey, formatAfter, mergeFeed, parseWatermark, FEED_TYPES } from './feed';

const feed = fixture<OverviewFeed>('overview_feed');
const keys = (items: FeedItem[]) => items.map((i) => i.key);

const item = (key: string, at: string, over: Partial<FeedItem> = {}): FeedItem => ({
	key,
	at,
	account_id: 1,
	type: 'battle',
	kind: 'battle',
	level: null,
	text: null,
	amounts: { money: 1 },
	items: {},
	...over
});

describe('mergeFeed', () => {
	it('новые записи сверху, порядок по времени события', () => {
		const merged = mergeFeed(feed.items.slice(2), feed.items.slice(0, 2));
		expect(keys(merged)).toEqual(keys(feed.items));
	});

	it('повторы по ключу не дублируются; запись приходит с тем же содержимым', () => {
		const merged = mergeFeed(feed.items, [...feed.items.slice(0, 3), ...feed.items]);
		expect(keys(merged)).toEqual(keys(feed.items));
	});

	it('поздняя запись со старым временем встаёт на своё место, а не наверх', () => {
		const late = item('l:48300', '2026-10-17T08:20:00+00:00', { account_id: 3 });
		const merged = mergeFeed(feed.items, [late]);
		const at = keys(merged).indexOf('l:48300');
		expect(keys(merged).slice(at - 1, at + 2)).toEqual(['l:48205', 'l:48300', 'l:48199']);
	});

	it('загруженный хвост сохраняется при приходе новых', () => {
		const tail = feed.items.slice(5);
		const merged = mergeFeed(tail, [item('l:50000', '2026-10-17T09:05:00+00:00')]);
		expect(keys(merged)).toEqual(['l:50000', ...keys(tail)]);
	});

	it('равное время: уведомления раньше журнала, внутри источника id по убыванию (как на сервере)', () => {
		const at = '2026-10-17T08:00:00+00:00';
		const merged = mergeFeed([], [item('l:9', at), item('n:3', at), item('l:12', at), item('n:10', at)]);
		expect(keys(merged)).toEqual(['n:10', 'n:3', 'l:12', 'l:9']);
	});

	it('смешанный часовой пояс записи сравнивается по моменту, а не по строке', () => {
		const a = item('l:1', '2026-10-17T11:00:00+03:00'); // 08:00Z
		const b = item('l:2', '2026-10-17T07:30:00+00:00');
		expect(keys(mergeFeed([], [b, a]))).toEqual(['l:1', 'l:2']);
	});

	it('не меняет аргументы', () => {
		const current = feed.items.slice(0, 2);
		const copy = [...current];
		mergeFeed(current, feed.items);
		expect(current).toEqual(copy);
	});
});

describe('знак приёма', () => {
	it('formatAfter и parseWatermark обратны друг другу', () => {
		expect(formatAfter({ ledger: 48211, notifications: 9120 })).toBe('48211:9120');
		expect(parseWatermark('48211:9120')).toEqual({ ledger: 48211, notifications: 9120 });
		expect(parseWatermark('0:0')).toEqual({ ledger: 0, notifications: 0 });
	});

	it('мусор не разбирается', () => {
		for (const bad of ['', '5', 'a:b', '1:2:3', '-1:2', '1.5:2']) expect(parseWatermark(bad)).toBeNull();
	});
});

describe('filtersKey', () => {
	it('не зависит от порядка выбора', () => {
		expect(filtersKey({ accountIds: [3, 1], types: ['alert', 'battle'] })).toBe(
			filtersKey({ accountIds: [1, 3], types: ['battle', 'alert'] })
		);
	});

	it('разные наборы - разные ключи; пустой набор - «все»', () => {
		const all = filtersKey({ accountIds: [], types: [] });
		expect(filtersKey({ accountIds: [1], types: [] })).not.toBe(all);
		expect(filtersKey({ accountIds: [], types: ['metro'] })).not.toBe(all);
		expect(filtersKey({ accountIds: [], types: [...FEED_TYPES.map((t) => t.type)] })).toBe(all);
	});
});

describe('текст события', () => {
	it('журнал: вид и суммы; предметы с количеством', () => {
		expect(feedText(feed.items[1]!)).toBe('⚔ битва · +$1 400, +800 💡');
		expect(feedText(feed.items[4]!)).toContain('🏭 фабрика');
		expect(feedText(feed.items[4]!)).toContain('Конденсатор ×1');
	});

	it('журнал без сумм: только вид', () => {
		expect(feedText(feed.items[3]!)).toBe('🔨 новый уровень');
	});

	it('неизвестный вид печатается как есть', () => {
		expect(feedText(item('l:1', '2026-10-17T08:00:00+00:00', { type: 'battle', kind: 'zzz_new', amounts: { money: 5 } }))).toBe(
			'zzz_new · +$5'
		);
	});

	it('уведомление: текст как есть', () => {
		expect(feedText(feed.items[0]!)).toBe('scenario deed:learn failed: gateway refused the command');
		expect(feedText(item('n:1', '2026-10-17T08:00:00+00:00', { type: 'manual', kind: 'engine_paused', text: null, level: 'info' }))).toBe(
			'engine_paused'
		);
	});
});

describe('значок', () => {
	it('журнал - значок вида, уведомления - по уровню и типу', () => {
		expect(feedIcon(feed.items[1]!)).toBe('⚔');
		expect(feedIcon(feed.items[0]!)).toBe('⛔');
		expect(feedIcon(feed.items[5]!)).toBe('⚠️');
		expect(feedIcon(feed.items[2]!)).toBe('✋');
		expect(feedIcon(item('l:1', '2026-10-17T08:00:00+00:00', { kind: 'zzz_new' }))).toBe('•');
	});
});
