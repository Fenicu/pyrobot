import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { DailyOut } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { DailyStore, untilMskMidnight } from './store.svelte';

const daily = fixture<DailyOut>('daily');

/** Сервер, который отвечает, когда скажет тест; запоминает запросы и сигналы отмены. */
function server() {
	const pending: Deferred<Response>[] = [];
	const requests: Request[] = [];
	const fetchImpl = (input: RequestInfo | URL) => {
		requests.push(input as Request);
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetchImpl as typeof fetch);
	const answer = async (value: unknown = daily, status = 200) => {
		pending.shift()?.resolve(json(value, status));
		await vi.advanceTimersByTimeAsync(0);
	};
	return { api, requests, answer, count: () => requests.length };
}

const tick = () => vi.advanceTimersByTimeAsync(0);
const state: LiveEvent = { type: 'state', id: '1', data: { version: 1, changed: { money: { value: 5 } } } };
const reset: LiveEvent = { type: 'reset', id: '', data: { reason: 'evicted' } };

describe('перечитывание итогов дня', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-28T11:40:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('запрос с числом дней; загрузка и ошибка', async () => {
		const s = server();
		const store = new DailyStore(s.api, 1);
		store.start();
		await tick();
		expect(store.loading).toBe(true);
		expect(new URL(s.requests[0]!.url).search).toBe('?days=1');
		await s.answer();
		expect(store.loading).toBe(false);
		expect(store.data?.days[0]?.day).toBe('2026-09-28');
		expect(store.loadedAt).toEqual(new Date('2026-09-28T11:40:00Z'));
		await vi.advanceTimersByTimeAsync(60_000);
		await s.answer({ detail: 'boom' }, 500);
		// Прежние данные остаются на экране, ошибка — рядом, время загрузки — последней удачной.
		expect(store.data?.days[0]?.day).toBe('2026-09-28');
		expect(store.error).not.toBeNull();
		expect(store.loadedAt).toEqual(new Date('2026-09-28T11:40:00Z'));
		store.stop();
	});

	it('раз в минуту; на кадр state — не чаще раза в 30 с', async () => {
		const s = server();
		const store = new DailyStore(s.api, 30);
		store.start();
		await tick();
		await s.answer();
		store.onEvent(state);
		await vi.advanceTimersByTimeAsync(29_900);
		expect(s.count()).toBe(1);
		await vi.advanceTimersByTimeAsync(100);
		expect(s.count()).toBe(2);
		await s.answer();
		// Минута от прошлого таймера минуты — ещё один запрос.
		await vi.advanceTimersByTimeAsync(30_000);
		expect(s.count()).toBe(3);
		await s.answer();
		store.onEvent(state);
		store.onEvent(state);
		await vi.advanceTimersByTimeAsync(30_000);
		expect(s.count()).toBe(4);
		store.stop();
	});

	it('reset — сразу, запрос в полёте отменяется и его поздний ответ не применяется', async () => {
		const s = server();
		const store = new DailyStore(s.api, 1);
		store.start();
		await tick();
		store.onEvent(reset);
		await tick();
		expect(s.requests[0]!.signal.aborted).toBe(true);
		expect(s.count()).toBe(2);
		await s.answer({ ...daily, ledger_since: '2000-01-01' });
		expect(store.data).toBeNull();
		await s.answer();
		expect(store.data?.ledger_since).toBe(daily.ledger_since);
		store.stop();
	});

	it('смена суток в полночь МСК — перечитывание', async () => {
		vi.setSystemTime(new Date('2026-09-28T20:59:30Z'));
		const s = server();
		const store = new DailyStore(s.api, 1, 3_600_000);
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(29_000);
		expect(s.count()).toBe(1);
		await vi.advanceTimersByTimeAsync(2_000);
		expect(s.count()).toBe(2);
		store.stop();
	});

	it('до полуночи МСК', () => {
		expect(untilMskMidnight(new Date('2026-09-28T20:59:30Z').getTime())).toBe(30_000);
		expect(untilMskMidnight(new Date('2026-09-28T21:00:00Z').getTime())).toBe(86_400_000);
	});

	it('уход со страницы — без запросов', async () => {
		const s = server();
		const store = new DailyStore(s.api, 1);
		store.start();
		await tick();
		store.stop();
		expect(s.requests[0]!.signal.aborted).toBe(true);
		store.onEvent(state);
		store.onEvent(reset);
		await vi.advanceTimersByTimeAsync(24 * 3_600_000);
		expect(s.count()).toBe(1);
	});
});
