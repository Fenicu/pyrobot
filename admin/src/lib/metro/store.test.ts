import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { MetroLive } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { liveFrame } from '$lib/test/metro-live';
import { MetroLiveStore } from './store.svelte';

/** Сервер, который отвечает, когда скажет тест. */
function server() {
	const pending: Deferred<Response>[] = [];
	const urls: string[] = [];
	const fetchImpl = (input: RequestInfo | URL) => {
		urls.push(new URL((input as Request).url).pathname);
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetchImpl as typeof fetch);
	const answer = async (response: Response) => {
		pending.shift()?.resolve(response);
		await vi.advanceTimersByTimeAsync(0);
	};
	return { api, urls, answer };
}

const tick = () => vi.advanceTimersByTimeAsync(0);
const live = (data: MetroLive): LiveEvent => ({ type: 'metro_live', id: 'e', data });
const reset: LiveEvent = { type: 'reset', id: 'r', data: { reason: 'new' } };

describe('живой кадр метро', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-10-07T18:10:00Z') }));
	afterEach(() => vi.useRealTimers());

	async function started() {
		const s = server();
		const store = new MetroLiveStore(s.api);
		store.start();
		await tick();
		return { s, store };
	}

	it('GET 204 — кадра нет; GET 200 — кадр и момент получения', async () => {
		const { s, store } = await started();
		expect(s.urls).toEqual(['/api/v1/accounts/1/metro/live']);
		await s.answer(new Response(null, { status: 204 }));
		expect(store.frame).toBeNull();
		store.onEvent(reset);
		await tick();
		await s.answer(json(liveFrame()));
		expect(store.frame?.steps).toBe(2);
		expect(store.receivedAt).toBe(Date.parse('2026-10-07T18:10:00Z'));
		store.stop();
	});

	it('204 после reset (движок перезапущен) снимает кадр прошлого запуска; кадр потока после запроса — нет', async () => {
		const { s, store } = await started();
		await s.answer(json(liveFrame()));
		vi.advanceTimersByTime(1_000);
		store.onEvent(reset);
		await tick();
		await s.answer(new Response(null, { status: 204 }));
		expect(store.frame).toBeNull();
		expect(store.receivedAt).toBeNull();
		vi.advanceTimersByTime(1_000);
		store.onEvent(reset);
		await tick();
		vi.advanceTimersByTime(1_000);
		store.onEvent(live(liveFrame({ message_id: 600 })));
		await s.answer(new Response(null, { status: 204 }));
		expect(store.frame?.message_id).toBe(600);
		store.stop();
	});

	it('503 — кадра нет, без ошибки на главной', async () => {
		const { s, store } = await started();
		await s.answer(json({ error: 'engine not running' }, 503));
		expect(store.frame).toBeNull();
		store.stop();
	});

	it('кадр потока обновляет, кадр другого забега заменяет', async () => {
		const { s, store } = await started();
		await s.answer(json(liveFrame()));
		vi.advanceTimersByTime(3_000);
		store.onEvent(live(liveFrame({ steps: 3, pos: [0, 2] })));
		expect(store.frame?.steps).toBe(3);
		expect(store.receivedAt).toBe(Date.parse('2026-10-07T18:10:03Z'));
		store.onEvent(live(liveFrame({ message_id: 600, steps: 0, events: [] })));
		expect(store.frame?.message_id).toBe(600);
		expect(store.frame?.events).toEqual([]);
		store.stop();
	});

	it('reset перечитывает GET; поздний ответ не затирает более свежий кадр потока', async () => {
		const { s, store } = await started();
		await s.answer(json(liveFrame()));
		store.onEvent(reset);
		await tick();
		expect(s.urls).toHaveLength(2);
		store.onEvent(live(liveFrame({ steps: 5 })));
		// Ответ снят до кадра потока: шагов меньше — не применяется.
		await s.answer(json(liveFrame({ steps: 4 })));
		expect(store.frame?.steps).toBe(5);
		// Прошлый забег (меньший message_id) не вытесняет текущий.
		store.onEvent(reset);
		await tick();
		await s.answer(json(liveFrame({ message_id: 400, steps: 90, running: false, outcome: 'finished' })));
		expect(store.frame?.message_id).toBe(500);
		store.stop();
	});

	it('при равных шагах конец забега важнее идущего кадра', async () => {
		const { s, store } = await started();
		await s.answer(json(liveFrame()));
		store.onEvent(live(liveFrame({ running: false, outcome: 'paused' })));
		store.onEvent(live(liveFrame()));
		expect(store.frame).toMatchObject({ running: false, outcome: 'paused' });
		store.stop();
	});

	it('события забега копятся: кадр несёт последние 30, карта — все с открытия главной', async () => {
		const { s, store } = await started();
		const e = (step: number) => ({ step, pos: [0, 0], kind: 'metro_loot', item: 'money', amount: step });
		await s.answer(json(liveFrame({ steps: 30, events: Array.from({ length: 30 }, (_, i) => e(i + 1)) })));
		store.onEvent(live(liveFrame({ steps: 32, events: Array.from({ length: 30 }, (_, i) => e(i + 3)) })));
		expect(store.frame?.events.map((x) => (x as { step: number }).step)).toEqual(Array.from({ length: 32 }, (_, i) => i + 1));
		// Новый забег начинает ленту заново.
		store.onEvent(live(liveFrame({ message_id: 501, steps: 1, events: [e(1)] })));
		expect(store.frame?.events).toHaveLength(1);
		store.stop();
	});

	it('после ухода с главной поздний ответ и кадры не применяются', async () => {
		const { s, store } = await started();
		store.stop();
		await s.answer(json(liveFrame()));
		store.onEvent(live(liveFrame()));
		expect(store.frame).toBeNull();
	});
});
