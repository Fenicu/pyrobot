import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { FeedItem, OverviewFeed } from '$lib/api/types';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { FEED_POLL_MS, FeedStore } from './feed.svelte';

const first = fixture<OverviewFeed>('overview_feed');

const row = (key: string, at: string, over: Partial<FeedItem> = {}): FeedItem => ({
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

const page = (over: Partial<OverviewFeed> = {}): OverviewFeed => ({
	items: [],
	next_cursor: null,
	watermark: first.watermark,
	has_more: false,
	...over
});

/** Сервер, который отвечает, когда скажет тест; запоминает запросы. */
function server() {
	const pending: Deferred<Response>[] = [];
	const requests: Request[] = [];
	const fetchImpl = async (input: RequestInfo | URL) => {
		requests.push(input as Request);
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createApi({ csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} }, fetchImpl as typeof fetch);
	const answer = async (value: unknown = first, status = 200) => {
		pending.shift()?.resolve(json(value, status));
		await vi.advanceTimersByTimeAsync(0);
	};
	const query = (n: number) => Object.fromEntries(new URL(requests[n]!.url).searchParams);
	return { api, requests, answer, query, count: () => requests.length };
}

const tick = () => vi.advanceTimersByTimeAsync(0);
const keys = (store: FeedStore) => store.items.map((i) => i.key);

describe('лента: первая страница и догрузка', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:00:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('при старте читает первую страницу без курсора и знака; запоминает знак и курсор', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		expect(new URL(s.requests[0]!.url).pathname).toBe('/api/v1/overview/feed');
		expect(s.query(0)).toEqual({ limit: '50' });
		expect(store.loading).toBe(true);
		await s.answer();
		expect(keys(store)).toEqual(first.items.map((i) => i.key));
		expect(store.loading).toBe(false);
		expect(store.hasOlder).toBe(true);
		store.stop();
	});

	it('«Загрузить ещё» идёт по курсору, знак приёма не меняется', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		await s.answer();
		const older = row('l:100', '2026-10-15T10:00:00+00:00');
		const more = store.loadMore();
		await tick();
		expect(s.query(1)).toEqual({ limit: '50', cursor: first.next_cursor });
		expect(store.loadingMore).toBe(true);
		await s.answer(page({ items: [older], next_cursor: null, watermark: null }));
		await more;
		expect(keys(store).at(-1)).toBe('l:100');
		expect(store.hasOlder).toBe(false);
		// Следующий опрос идёт со знаком первой страницы.
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(2).after).toBe('48211:9120');
		store.stop();
	});

	it('без следующей страницы «Загрузить ещё» ничего не запрашивает', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		await s.answer(page({ items: first.items.slice(0, 2), next_cursor: null }));
		await store.loadMore();
		expect(s.count()).toBe(1);
		store.stop();
	});

	it('сбой первой страницы: пусто и ошибка; следующий опрос читает первую страницу заново', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		await s.answer({ detail: 'boom' }, 500);
		expect(store.items).toEqual([]);
		expect(store.error).not.toBeNull();
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(1)).toEqual({ limit: '50' });
		await s.answer();
		expect(store.error).toBeNull();
		expect(store.items).toHaveLength(first.items.length);
		store.stop();
	});
});

describe('лента: опрос по знаку приёма', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:00:00Z') }));
	afterEach(() => vi.useRealTimers());

	async function loaded() {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		await s.answer();
		return { s, store };
	}

	it('раз в 30 с запрашивает записи после знака и вставляет их по времени события', async () => {
		const { s, store } = await loaded();
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(1)).toEqual({ limit: '100', after: '48211:9120' });
		const late = row('l:48300', '2026-10-17T08:20:00+00:00');
		const fresh = row('n:9200', '2026-10-17T09:00:00+00:00', { key: 'n:9200', type: 'alert', level: 'warn', text: 'x', amounts: null, items: null });
		await s.answer(page({ items: [late, fresh], watermark: { ledger: 48300, notifications: 9200 } }));
		expect(keys(store).slice(0, 2)).toEqual(['n:9200', 'n:9120']);
		const at = keys(store).indexOf('l:48300');
		expect(keys(store).slice(at - 1, at + 2)).toEqual(['l:48205', 'l:48300', 'l:48199']);
		// Следующий опрос - с новым знаком.
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(2).after).toBe('48300:9200');
		store.stop();
	});

	it('пока has_more, повторяет запрос сразу; потом ждёт таймер', async () => {
		const { s, store } = await loaded();
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		await s.answer(page({ items: [row('l:48220', '2026-10-17T08:51:00+00:00')], watermark: { ledger: 48220, notifications: 9120 }, has_more: true }));
		expect(s.count()).toBe(3);
		expect(s.query(2).after).toBe('48220:9120');
		await s.answer(page({ items: [row('l:48230', '2026-10-17T08:52:00+00:00')], watermark: { ledger: 48230, notifications: 9120 }, has_more: false }));
		expect(s.count()).toBe(3);
		expect(keys(store)).toContain('l:48230');
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(3).after).toBe('48230:9120');
		store.stop();
	});

	it('ошибка опроса не стирает ленту; знак остаётся прежним', async () => {
		const { s, store } = await loaded();
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		await s.answer({ detail: 'boom' }, 500);
		expect(store.items).toHaveLength(first.items.length);
		expect(store.error).not.toBeNull();
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(2).after).toBe('48211:9120');
		await s.answer(page({ watermark: first.watermark }));
		expect(store.error).toBeNull();
		store.stop();
	});

	it('один запрос в полёте: опрос во время запроса - один после него', async () => {
		const { s, store } = await loaded();
		void store.poll();
		void store.poll();
		void store.poll();
		await tick();
		expect(s.count()).toBe(2);
		await s.answer(page());
		expect(s.count()).toBe(3);
		await s.answer(page());
		expect(s.count()).toBe(3);
		store.stop();
	});
});

describe('лента: фильтры и остановка', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:00:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('смена фильтров начинает ленту заново: новая первая страница и знак, поздний ответ не применяется', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		await s.answer();
		void store.poll();
		await tick();
		store.setFilters({ accountIds: [3, 1], types: ['alert', 'battle'] });
		await tick();
		expect(s.requests[1]!.signal.aborted).toBe(true);
		expect(s.query(2)).toEqual({ limit: '50', account_ids: '1,3', types: 'battle,alert' });
		expect(store.items).toEqual([]);
		await s.answer(page({ items: [row('l:1', '2026-10-17T08:00:00+00:00')] })); // поздний ответ старого опроса
		expect(store.items).toEqual([]);
		const mine = page({ items: [row('l:7', '2026-10-17T08:30:00+00:00')], watermark: { ledger: 7, notifications: 0 } });
		await s.answer(mine);
		expect(keys(store)).toEqual(['l:7']);
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.query(3)).toEqual({ limit: '100', after: '7:0', account_ids: '1,3', types: 'battle,alert' });
		store.stop();
	});

	it('те же фильтры в другом порядке или «все типы» - не перезапуск', async () => {
		const s = server();
		const store = new FeedStore(s.api, { accountIds: [1, 3], types: [] });
		store.start();
		await tick();
		await s.answer();
		store.setFilters({ accountIds: [3, 1], types: [] });
		store.setFilters({ accountIds: [1, 3], types: ['levelup', 'battle', 'factory', 'metro', 'lottery', 'alert', 'manual'] });
		await tick();
		expect(s.count()).toBe(1);
		expect(store.items).toHaveLength(first.items.length);
		store.stop();
	});

	it('смена фильтров во время «Загрузить ещё»: ответ догрузки отбрасывается', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		await s.answer();
		void store.loadMore();
		await tick();
		store.setFilters({ accountIds: [2], types: [] });
		await tick();
		await s.answer(page({ items: [row('l:100', '2026-10-15T10:00:00+00:00')] }));
		expect(store.items).toEqual([]);
		expect(store.loadingMore).toBe(false);
		store.stop();
	});

	it('stop(): таймер погашен, запрос отменён, поздний ответ отброшен', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		await tick();
		store.stop();
		expect(s.requests[0]!.signal.aborted).toBe(true);
		await s.answer();
		expect(store.items).toEqual([]);
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS * 5);
		expect(s.count()).toBe(1);
	});

	it('повторный start() не плодит таймеры', async () => {
		const s = server();
		const store = new FeedStore(s.api);
		store.start();
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(FEED_POLL_MS);
		expect(s.count()).toBe(2);
		store.stop();
	});
});
