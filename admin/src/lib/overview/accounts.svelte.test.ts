import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { OverviewAccount } from '$lib/api/types';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { OVERVIEW_POLL_MS, OverviewAccountsStore } from './accounts.svelte';

const rows = fixture<OverviewAccount[]>('overview_accounts');

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
	const answer = async (value: unknown = rows, status = 200) => {
		pending.shift()?.resolve(json(value, status));
		await vi.advanceTimersByTimeAsync(0);
	};
	return { api, requests, answer, count: () => requests.length };
}

const tick = () => vi.advanceTimersByTimeAsync(0);

describe('опрос сводки по аккаунтам', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:18:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('читает при старте и раз в 30 с; ошибка не стирает прежнее', async () => {
		const s = server();
		const store = new OverviewAccountsStore(s.api);
		store.start();
		await tick();
		expect(new URL(s.requests[0]!.url).pathname).toBe('/api/v1/overview/accounts');
		await s.answer();
		expect(store.data).toHaveLength(4);
		expect(store.loadedAt).toEqual(new Date('2026-10-17T09:18:00Z'));
		await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS);
		expect(s.count()).toBe(2);
		await s.answer({ detail: 'boom' }, 500);
		expect(store.data).toHaveLength(4);
		expect(store.error).not.toBeNull();
		await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS);
		await s.answer();
		expect(store.error).toBeNull();
		store.stop();
	});

	it('один запрос в полёте: повтор во время запроса даёт один после него', async () => {
		const s = server();
		const store = new OverviewAccountsStore(s.api);
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
		const store = new OverviewAccountsStore(s.api);
		store.start();
		await tick();
		store.stop();
		expect(s.requests[0]!.signal.aborted).toBe(true);
		await s.answer();
		expect(store.data).toBeNull();
		await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS * 3);
		expect(s.count()).toBe(1);
	});

	it('повторный start() не плодит таймеры', async () => {
		const s = server();
		const store = new OverviewAccountsStore(s.api);
		store.start();
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(OVERVIEW_POLL_MS);
		expect(s.count()).toBe(2);
		store.stop();
	});

	it('после stop() можно запустить снова', async () => {
		const s = server();
		const store = new OverviewAccountsStore(s.api);
		store.start();
		await tick();
		store.stop();
		store.start();
		await tick();
		expect(s.count()).toBe(2);
		await s.answer();
		await s.answer();
		expect(store.data).toHaveLength(4);
		store.stop();
	});
});
