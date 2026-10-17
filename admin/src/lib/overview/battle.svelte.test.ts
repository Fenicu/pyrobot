import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { BattleHq, BattleOrdersSaved } from '$lib/api/types';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { BATTLE_POLL_MS, BattleStore } from './battle.svelte';

const hq = fixture<BattleHq>('overview_battle');

/** Сервер, который отвечает, когда скажет тест; запоминает запросы. */
function server() {
	const pending: Deferred<Response>[] = [];
	const requests: Request[] = [];
	const bodies: string[] = [];
	const fetchImpl = async (input: RequestInfo | URL) => {
		const request = input as Request;
		requests.push(request);
		bodies.push(request.method === 'GET' ? '' : await request.clone().text());
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createApi({ csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} }, fetchImpl as typeof fetch);
	const answer = async (value: unknown = hq, status = 200) => {
		pending.shift()?.resolve(json(value, status));
		await vi.advanceTimersByTimeAsync(0);
	};
	return { api, requests, bodies, answer, count: () => requests.length };
}

const tick = () => vi.advanceTimersByTimeAsync(0);
const PATH = '/api/v1/overview/battle';

describe('опрос штаба битвы', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:18:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('читает при старте и раз в 30 с; ошибка не стирает прежнее', async () => {
		const s = server();
		const store = new BattleStore(s.api);
		store.start();
		await tick();
		expect(new URL(s.requests[0]!.url).pathname).toBe(PATH);
		await s.answer();
		expect(store.data?.battles).toHaveLength(2);
		expect(store.loadedAt).toEqual(new Date('2026-10-17T09:18:00Z'));
		await vi.advanceTimersByTimeAsync(BATTLE_POLL_MS);
		expect(s.count()).toBe(2);
		await s.answer({ detail: 'boom' }, 500);
		expect(store.data?.battles).toHaveLength(2);
		expect(store.error).not.toBeNull();
		await vi.advanceTimersByTimeAsync(BATTLE_POLL_MS);
		await s.answer();
		expect(store.error).toBeNull();
		store.stop();
	});

	it('один запрос в полёте: повтор во время запроса — один после него', async () => {
		const s = server();
		const store = new BattleStore(s.api);
		store.start();
		await tick();
		void store.load();
		void store.load();
		await tick();
		expect(s.count()).toBe(1);
		await s.answer();
		expect(s.count()).toBe(2);
		await s.answer();
		expect(s.count()).toBe(2);
		store.stop();
	});

	it('stop() останавливает таймер и отбрасывает поздний ответ', async () => {
		const s = server();
		const store = new BattleStore(s.api);
		store.start();
		await tick();
		store.stop();
		expect(s.requests[0]!.signal.aborted).toBe(true);
		await s.answer();
		expect(store.data).toBeNull();
		await vi.advanceTimersByTimeAsync(BATTLE_POLL_MS * 3);
		expect(s.count()).toBe(1);
	});

	it('повторный start() не плодит таймеры', async () => {
		const s = server();
		const store = new BattleStore(s.api);
		store.start();
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(BATTLE_POLL_MS);
		expect(s.count()).toBe(2);
		store.stop();
	});
});

describe('сохранение приказов', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:18:00Z') }));
	afterEach(() => vi.useRealTimers());

	const first = hq.battles[0]!;
	const saved = (orders: BattleOrdersSaved['orders']): BattleOrdersSaved => ({
		...first,
		orders,
		woken: [1, 2],
		not_woken: [{ account_id: 3, reason: 'locked_elsewhere' }]
	});

	async function loaded() {
		const s = server();
		const store = new BattleStore(s.api);
		store.start();
		await tick();
		await s.answer();
		return { s, store };
	}

	it('PUT с точным телом и CSRF; ответ подменяет битву, затем перечитывание', async () => {
		const { s, store } = await loaded();
		const body = { all: '🤖Hooli', corporations: { bmesa: '☂️Umbrella' } };
		const done = store.save(first.battle_at, body);
		await tick();
		const put = s.requests[1]!;
		expect(put.method).toBe('PUT');
		expect(decodeURIComponent(new URL(put.url).pathname)).toBe(`${PATH}/${first.battle_at}/orders`);
		expect(s.bodies[1]).toBe(JSON.stringify(body));
		expect(put.headers.get('X-CSRF-Token')).toBe('tok');
		expect(store.saving).toBe(true);
		await s.answer(saved({ all: '🤖Hooli', corporations: { bmesa: '☂️Umbrella' } }));
		expect(await done).toBe(true);
		expect(store.saving).toBe(false);
		expect(store.data?.battles[0]?.orders).toEqual({ all: '🤖Hooli', corporations: { bmesa: '☂️Umbrella' } });
		expect(store.data?.battles[1]).toEqual(hq.battles[1]);
		expect(store.result).toMatchObject({
			battleAt: first.battle_at,
			error: null,
			woken: [1, 2],
			notWoken: [{ account_id: 3, reason: 'locked_elsewhere' }]
		});
		// После записи данные перечитываются.
		expect(s.count()).toBe(3);
		expect(s.requests[2]!.method).toBe('GET');
		await s.answer();
		store.stop();
	});

	it('опрос во время записи ждёт её: устаревшее чтение не затирает ответ', async () => {
		const { s, store } = await loaded();
		const done = store.save(first.battle_at, { all: '🤖Hooli', corporations: {} });
		await tick();
		await vi.advanceTimersByTimeAsync(BATTLE_POLL_MS);
		// Новый GET не ушёл: идет только PUT.
		expect(s.requests.map((r) => r.method)).toEqual(['GET', 'PUT']);
		await s.answer(saved({ all: '🤖Hooli', corporations: {} }));
		await done;
		expect(s.requests.map((r) => r.method)).toEqual(['GET', 'PUT', 'GET']);
		store.stop();
	});

	it('запрос чтения в полёте на момент записи отбрасывается', async () => {
		const { s, store } = await loaded();
		void store.load();
		await tick();
		expect(s.requests.map((r) => r.method)).toEqual(['GET', 'GET']);
		const stale = structuredClone(hq);
		stale.battles[0]!.orders = { all: '📯Pied Piper', corporations: {} };
		const done = store.save(first.battle_at, { all: '🤖Hooli', corporations: {} });
		await tick();
		expect(s.requests[1]!.signal.aborted).toBe(true);
		await s.answer(stale);
		await s.answer(saved({ all: '🤖Hooli', corporations: {} }));
		await done;
		expect(store.data?.battles[0]?.orders.all).toBe('🤖Hooli');
		store.stop();
	});

	it('ошибка: данные прежние, ошибка у результата, и сразу перечитывание', async () => {
		const { s, store } = await loaded();
		const before = store.data;
		const done = store.save(first.battle_at, { all: null, corporations: { hooli: '🤖Hooli' } });
		await tick();
		await s.answer(
			{ detail: [{ loc: ['body', 'corporations', 'hooli'], msg: 'x', type: 'target_is_own_company' }] },
			422
		);
		expect(await done).toBe(false);
		expect(store.data).toEqual(before);
		expect(store.result?.error).toMatchObject({ kind: 'validation' });
		expect(store.result?.woken).toEqual([]);
		expect(s.requests.at(-1)!.method).toBe('GET');
		await s.answer();
		store.stop();
	});

	it('вторая запись во время первой не уходит; новая запись очищает прежний результат', async () => {
		const { s, store } = await loaded();
		const one = store.save(first.battle_at, { all: '🤖Hooli', corporations: {} });
		await tick();
		expect(await store.save(first.battle_at, { all: null, corporations: {} })).toBe(false);
		expect(s.requests.filter((r) => r.method === 'PUT')).toHaveLength(1);
		await s.answer(saved({ all: '🤖Hooli', corporations: {} }));
		await one;
		await s.answer();
		expect(store.result).not.toBeNull();
		const two = store.save(first.battle_at, { all: null, corporations: {} });
		await tick();
		expect(store.result).toBeNull();
		await s.answer(saved({ all: null, corporations: {} }));
		await two;
		await s.answer();
		store.stop();
	});
});
