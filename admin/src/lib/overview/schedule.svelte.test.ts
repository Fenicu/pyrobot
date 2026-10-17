import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { ScheduleOut } from '$lib/api/types';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { SCHEDULE_POLL_MS, ScheduleStore } from './schedule.svelte';

const sched = fixture<ScheduleOut>('overview_schedule');

function server() {
	const pending: Deferred<Response>[] = [];
	const requests: Request[] = [];
	const fetchImpl = async (input: RequestInfo | URL) => {
		const request = input as Request;
		requests.push(request);
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createApi(
		{ csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} },
		fetchImpl as typeof fetch
	);
	const answer = async (value: unknown = sched, status = 200) => {
		pending.shift()?.resolve(json(value, status));
		await vi.advanceTimersByTimeAsync(0);
	};
	return { api, requests, answer, count: () => requests.length };
}

const tick = () => vi.advanceTimersByTimeAsync(0);
const PATH = '/api/v1/overview/schedule';

describe('опрос расписания (ScheduleStore)', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-17T09:00:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('читает при старте и каждые 30 с; ошибка сохраняет прежние данные', async () => {
		const s = server();
		const store = new ScheduleStore(s.api);
		store.start();
		await tick();
		expect(new URL(s.requests[0]!.url).pathname).toBe(PATH);
		await s.answer();
		expect(store.data?.accounts).toHaveLength(2);
		expect(store.loadedAt).toEqual(new Date('2026-10-17T09:00:00Z'));

		await vi.advanceTimersByTimeAsync(SCHEDULE_POLL_MS);
		expect(s.count()).toBe(2);
		await s.answer({ detail: 'fail' }, 500);
		expect(store.data?.accounts).toHaveLength(2);
		expect(store.error).not.toBeNull();

		store.stop();
	});

	it('stop() останавливает таймер и отменяет запрос', async () => {
		const s = server();
		const store = new ScheduleStore(s.api);
		store.start();
		await tick();
		store.stop();
		await s.answer();
		// Поздний ответ отменённого запроса не применяется
		expect(store.data).toBeNull();

		await vi.advanceTimersByTimeAsync(SCHEDULE_POLL_MS * 2);
		expect(s.count()).toBe(1);
	});

	it('один запрос в полёте: повторные запросы во время загрузки сериализуются', async () => {
		const s = server();
		const store = new ScheduleStore(s.api);
		void store.load();
		void store.load();
		await tick();
		expect(s.count()).toBe(1);
		await s.answer();
		expect(s.count()).toBe(2);
		await s.answer();
		expect(s.count()).toBe(2);
	});
});
