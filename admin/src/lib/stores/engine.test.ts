import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { EngineStatus } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { ENGINE_POLL_MS, ENGINE_STARTING_POLL_MS, EngineStore } from './engine.svelte';

const hooks = { csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} };
const status = (over: Partial<EngineStatus>): EngineStatus => ({ ...fixture<EngineStatus>('engine_status'), ...over });
const starting = status({ running: false, status: 'enabled' });
const disabled = status({ running: false, status: 'disabled' });
const running = status({ running: true, status: 'enabled' });

/** Хранилище, которому сервер отвечает текущим `answer`; `polls()` — сколько было запросов. */
function rig(first: EngineStatus) {
	let answer = first;
	const fetch = mockFetch(() => json(answer));
	const store = new EngineStore(createAccountApi(hooks, 1, fetch));
	return {
		store,
		polls: () => fetch.calls.length,
		set: (next: EngineStatus) => (answer = next)
	};
}

afterEach(() => vi.useRealTimers());

describe('опрос статуса движка', () => {
	it('включённый, но не запущенный — раз в ~2 с, после старта — снова раз в 15 с', async () => {
		vi.useFakeTimers();
		const r = rig(starting);
		r.store.start();
		await vi.advanceTimersByTimeAsync(0);
		expect([r.polls(), r.store.status?.running]).toEqual([1, false]);
		await vi.advanceTimersByTimeAsync(ENGINE_STARTING_POLL_MS);
		expect(r.polls()).toBe(2);
		r.set(running);
		await vi.advanceTimersByTimeAsync(ENGINE_STARTING_POLL_MS);
		// Плашка «запускается» уходит сразу после старта.
		expect([r.polls(), r.store.status?.running]).toEqual([3, true]);
		await vi.advanceTimersByTimeAsync(ENGINE_POLL_MS - 1);
		expect(r.polls()).toBe(3);
		await vi.advanceTimersByTimeAsync(1);
		expect(r.polls()).toBe(4);
		r.store.stop();
		await vi.advanceTimersByTimeAsync(ENGINE_POLL_MS * 2);
		expect(r.polls()).toBe(4);
	});

	it('выключенный — раз в 15 с; после «Включить» перечитанный статус ускоряет опрос', async () => {
		vi.useFakeTimers();
		const r = rig(disabled);
		r.store.start();
		await vi.advanceTimersByTimeAsync(ENGINE_STARTING_POLL_MS * 3);
		expect(r.polls()).toBe(1);
		r.set(starting);
		await r.store.load();
		expect(r.polls()).toBe(2);
		await vi.advanceTimersByTimeAsync(ENGINE_STARTING_POLL_MS);
		expect(r.polls()).toBe(3);
		r.store.stop();
	});

	it('ответ после остановки опрос не возобновляет', async () => {
		vi.useFakeTimers();
		const r = rig(starting);
		r.store.start();
		r.store.stop();
		await vi.advanceTimersByTimeAsync(ENGINE_POLL_MS);
		expect(r.polls()).toBe(1);
	});
});
