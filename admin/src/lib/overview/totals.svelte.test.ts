import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { OverviewTotals } from '$lib/api/types';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { TOTALS_POLL_LONG_MS, TOTALS_POLL_TODAY_MS } from './totals';
import { TotalsStore } from './totals.svelte';

const week = fixture<OverviewTotals>('overview_totals');
const today: OverviewTotals = { ...week, days: 1, first: week.today };

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
	const answer = async (value: unknown = week, status = 200) => {
		pending.shift()?.resolve(json(value, status));
		await vi.advanceTimersByTimeAsync(0);
	};
	const asked = () => requests.map((r) => new URL(r.url).searchParams.get('days'));
	return { api, requests, answer, asked };
}

const tick = () => vi.advanceTimersByTimeAsync(0);

describe('опрос итогов', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:00:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('читает нужный период при старте; «Сегодня» опрашивает раз в 30 с', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 1);
		store.start();
		await tick();
		expect(new URL(s.requests[0]!.url).pathname).toBe('/api/v1/overview/totals');
		expect(s.asked()).toEqual(['1']);
		await s.answer(today);
		expect(store.data?.days).toBe(1);
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_TODAY_MS);
		expect(s.asked()).toEqual(['1', '1']);
		store.stop();
	});

	it('7 и 30 дней: раз в 5 минут', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 7);
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_TODAY_MS * 3);
		expect(s.asked()).toEqual(['7']);
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_LONG_MS);
		expect(s.asked()).toEqual(['7', '7']);
		store.stop();
	});

	it('ошибка не стирает прежние данные', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 7);
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_LONG_MS);
		await s.answer({ detail: 'boom' }, 500);
		expect(store.data?.days).toBe(7);
		expect(store.error).not.toBeNull();
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_LONG_MS);
		await s.answer();
		expect(store.error).toBeNull();
		store.stop();
	});

	it('смена периода: старый запрос отменён, его поздний ответ не применяется, данные сброшены', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 7);
		store.start();
		await tick();
		await s.answer();
		expect(store.data?.days).toBe(7);
		void store.load();
		await tick();
		store.setDays(1);
		await tick();
		expect(s.requests[1]!.signal.aborted).toBe(true);
		expect(s.asked()).toEqual(['7', '7', '1']);
		expect(store.data).toBeNull();
		await s.answer(week); // поздний ответ за 7 дней
		expect(store.data).toBeNull();
		await s.answer(today);
		expect(store.data?.days).toBe(1);
		// Интервал опроса - нового периода.
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_TODAY_MS);
		expect(s.asked()).toEqual(['7', '7', '1', '1']);
		store.stop();
	});

	it('тот же период — не перезапуск', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 7);
		store.start();
		await tick();
		await s.answer();
		store.setDays(7);
		await tick();
		expect(s.asked()).toEqual(['7']);
		expect(store.data).not.toBeNull();
		store.stop();
	});

	it('один запрос в полёте: повтор во время запроса — один после него', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 7);
		store.start();
		await tick();
		void store.load();
		void store.load();
		await tick();
		expect(s.asked()).toHaveLength(1);
		await s.answer();
		expect(s.asked()).toHaveLength(2);
		await s.answer();
		expect(s.asked()).toHaveLength(2);
		store.stop();
	});

	it('возвращение во вкладку перечитывает; после stop() не реагирует', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 30);
		store.start();
		await tick();
		await s.answer();
		document.dispatchEvent(new Event('visibilitychange'));
		await tick();
		expect(s.asked()).toEqual(['30', '30']);
		await s.answer();
		store.stop();
		document.dispatchEvent(new Event('visibilitychange'));
		await tick();
		expect(s.asked()).toHaveLength(2);
	});

	it('смена суток МСК перечитывает без ожидания опроса', async () => {
		vi.setSystemTime(new Date('2026-10-17T20:59:00Z')); // 23:59 МСК
		const s = server();
		const store = new TotalsStore(s.api, 30);
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(60_000);
		expect(s.asked()).toHaveLength(1);
		await vi.advanceTimersByTimeAsync(2_000);
		expect(s.asked()).toEqual(['30', '30']);
		await s.answer();
		// Следующая полночь взведена снова.
		await vi.advanceTimersByTimeAsync(86_400_000);
		expect(s.asked().length).toBeGreaterThanOrEqual(3);
		store.stop();
	});

	it('stop(): таймеры погашены, запрос отменён, поздний ответ отброшен', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 1);
		store.start();
		await tick();
		store.stop();
		expect(s.requests[0]!.signal.aborted).toBe(true);
		await s.answer(today);
		expect(store.data).toBeNull();
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_LONG_MS * 3 + 86_400_000);
		expect(s.asked()).toHaveLength(1);
	});

	it('повторный start() не плодит таймеры', async () => {
		const s = server();
		const store = new TotalsStore(s.api, 1);
		store.start();
		store.start();
		await tick();
		await s.answer(today);
		await vi.advanceTimersByTimeAsync(TOTALS_POLL_TODAY_MS);
		expect(s.asked()).toHaveLength(2);
		store.stop();
	});
});
