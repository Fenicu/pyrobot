import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { Outlook } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { PlanStore, significant } from './store.svelte';

const plan = fixture<Outlook>('outlook');

/** Сервер, который отвечает, когда скажет тест; запоминает сигналы отмены запросов. */
function server() {
	const pending: Deferred<Response>[] = [];
	const signals: AbortSignal[] = [];
	const fetchImpl = (input: RequestInfo | URL) => {
		const request = input as Request;
		signals.push(request.signal);
		const d = deferred<Response>();
		pending.push(d);
		return d.promise;
	};
	const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetchImpl as typeof fetch);
	const answer = async (value: Outlook = plan) => {
		pending.shift()?.resolve(json(value));
		await vi.advanceTimersByTimeAsync(0);
	};
	return { api, signals, answer, requests: () => signals.length };
}

/** Дать клиенту API дойти до fetch (промежуточные обработчики асинхронные). */
const tick = () => vi.advanceTimersByTimeAsync(0);

const state = (changed: Record<string, unknown>, version = 1): LiveEvent => ({
	type: 'state',
	id: String(version),
	data: { version, changed }
});
const decision: LiveEvent = {
	type: 'decision',
	id: 'd',
	data: { id: 1, at: '2026-09-27T16:30:00Z', kind: 'act', scenario: 'book', reason: 'book_ready', until: null }
};
const settingsFrame: LiveEvent = {
	type: 'settings',
	id: 's',
	data: { version: 2, mode: 'live', paused: true, killed: false }
};

describe('перечитывание плана', () => {
	beforeEach(() => vi.useFakeTimers({ now: new Date('2026-09-27T16:30:00Z') }));
	afterEach(() => vi.useRealTimers());

	it('один запрос в полёте: повторы во время него — один запрос после', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		expect(s.requests()).toBe(1);
		store.onEvent(decision);
		store.onEvent({ type: 'scenario_run', id: 'r', data: { id: 1, scenario: 'book', status: 'done', reason: '' } });
		await tick();
		expect(s.requests()).toBe(1);
		await s.answer();
		await tick();
		expect(store.outlook?.decision.scenario).toBe('lottery_buy');
		expect(s.requests()).toBe(2);
		await s.answer();
		expect(s.requests()).toBe(2);
		store.stop();
	});

	it('кадр settings перечитывает план сразу — пауза, продолжение, kill, смена режима, PATCH настроек', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		await s.answer();
		expect(s.requests()).toBe(1);
		store.onEvent(settingsFrame);
		await tick();
		expect(s.requests()).toBe(2);
		store.stop();
	});

	it('кадр state: только значимые поля и не чаще раза в 5 с', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		await s.answer();
		store.onEvent(state({ exp: { value: 1 }, skills: {} }));
		await vi.advanceTimersByTimeAsync(10_000);
		expect(s.requests()).toBe(1);
		store.onEvent(state({ money: { value: 5 } }));
		store.onEvent(state({ motivation_next_at: { value: 'x' } }, 2));
		await tick();
		// Последний запрос — 10 с назад: сразу, а следующий — только через 5 с после него.
		expect(s.requests()).toBe(2);
		await s.answer();
		store.onEvent(state({ busy: null }, 3));
		store.onEvent(state({ lottery: {} }, 4));
		await vi.advanceTimersByTimeAsync(4_900);
		expect(s.requests()).toBe(2);
		await vi.advanceTimersByTimeAsync(100);
		expect(s.requests()).toBe(3);
		store.stop();
	});

	it('readyChanged перечитывает план с тем же троттлингом, что и значимые кадры state', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		await s.answer();
		store.readyChanged();
		await vi.advanceTimersByTimeAsync(4_900);
		expect(s.requests()).toBe(1);
		await vi.advanceTimersByTimeAsync(100);
		expect(s.requests()).toBe(2);
		store.stop();
	});

	it('раз в минуту и после reset', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		await s.answer();
		await vi.advanceTimersByTimeAsync(60_000);
		expect(s.requests()).toBe(2);
		await s.answer();
		store.onEvent({ type: 'reset', id: '', data: { reason: 'evicted' } });
		await tick();
		expect(s.requests()).toBe(3);
		store.stop();
	});

	it('reset: прежний план виден, пока не придёт новый; запрос отменяется, поздний ответ прежнего поколения не применяется', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		await s.answer();
		expect(store.outlook?.decision.scenario).toBe('lottery_buy');
		store.onEvent(decision);
		await tick();
		expect(s.requests()).toBe(2);
		store.onEvent({ type: 'reset', id: '', data: { reason: 'epoch' } });
		await tick();
		// Блок не мигает «Загрузка плана…»: старый план остаётся, пока не пришёл новый.
		expect(store.outlook?.decision.scenario).toBe('lottery_buy');
		expect(s.signals[1]?.aborted).toBe(true);
		expect(s.requests()).toBe(3);
		// Ответ на запрос до reset приходит позже — не применяется (был бы виден как 'gorbushka').
		await s.answer({ ...plan, decision: { ...plan.decision, scenario: 'gorbushka' } });
		expect(store.outlook?.decision.scenario).toBe('lottery_buy');
		// Ответ нового поколения (после reset) применяется.
		await s.answer({ ...plan, decision: { ...plan.decision, scenario: 'book' } });
		expect(store.outlook?.decision.scenario).toBe('book');
		expect(s.requests()).toBe(3);
		store.stop();
	});

	it('уход с главной отменяет запрос и перечитывания', async () => {
		const s = server();
		const store = new PlanStore(s.api);
		store.start();
		await tick();
		store.onEvent(decision);
		store.stop();
		expect(s.signals[0]?.aborted).toBe(true);
		store.onEvent(decision);
		store.onEvent(state({ money: 1 }));
		await vi.advanceTimersByTimeAsync(120_000);
		expect(s.requests()).toBe(1);
		expect(store.outlook).toBeNull();
	});
});

describe('значимые поля state', () => {
	it('всё, что читает планировщик, в том числе цены, статистика дел, биржа, уровень и гаджеты', () => {
		for (const field of [
			'busy', 'motivation', 'money', 'details', 'book_ready_at', 'sleep_deadline', 'team_task', 'lottery',
			'battle_at', 'battle_target', 'prices', 'activity_stats', 'stock_limits', 'stock_quotes',
			'smoothie_ingredients', 'level', 'gadgets', 'bag', 'bag_cap', 'upgrades', 'stock_holdings',
			'some_future_field'
		]) {
			expect(significant({ [field]: null }), field).toBe(true);
		}
	});

	it('поля, которых планировщик не читает, план не перечитывают', () => {
		expect(significant({ exp: 1, skills: {}, glory: 5 })).toBe(false);
		expect(significant({ tangerines: 3 })).toBe(true);
		expect(significant({ exp: 1, money: 5 })).toBe(true);
	});
});
