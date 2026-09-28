import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { Outlook } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

export const PLAN_RELOAD_MS = 60_000;
/** Кадры `state` идут пачками: план из-за них — не чаще раза в 5 с (и кеш сервера — 5 с). */
export const PLAN_STATE_GAP_MS = 5_000;

/** Поля состояния, от которых зависит план: занятость, 🔥, деньги, ресурсы, таймеры, задания,
 * лотерея, битва. Прочие (опыт, навыки, рюкзак) план не меняют. */
const SIGNIFICANT = new Set([
	'busy',
	'motivation',
	'money',
	'stamina',
	'knowledge',
	'raw',
	'details',
	'levelup_pending',
	'books',
	'cards',
	'prizebox',
	'containers_small',
	'containers_medium',
	'food_stock',
	'gorbushka',
	'daily_personal',
	'team_task',
	'lottery',
	'battle_target',
	'battle_target_set',
	'metro_message',
	'factory_signed',
	'factory_skip',
	'bulls_invite',
	'smoothie_recipe',
	'smoothie_bonus',
	'tangerine_not_player'
]);

export function significant(changed: Record<string, unknown>): boolean {
	return Object.keys(changed).some((k) => SIGNIFICANT.has(k) || k.endsWith('_at') || k.endsWith('_deadline'));
}

/** «План бота» (`GET /planner/outlook`): один запрос в полёте на вкладку (повтор во время запроса —
 * ещё один после него), отмена при уходе с главной. Перечитывание: при открытии, на кадр
 * `decision` и `scenario_run`, на кадр `state` со значимыми полями — не чаще раза в 5 с, раз в
 * минуту, после `reset`. */
export class PlanStore {
	outlook = $state<Outlook | null>(null);
	error = $state<ApiError | null>(null);
	#api: Api;
	#reloadMs: number;
	#gapMs: number;
	#clock: () => number;
	#timer: ReturnType<typeof setInterval> | null = null;
	#deferred: ReturnType<typeof setTimeout> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#started = false;
	#lastLoad = Number.NEGATIVE_INFINITY;

	constructor(api: Api, reloadMs = PLAN_RELOAD_MS, gapMs = PLAN_STATE_GAP_MS, clock: () => number = Date.now) {
		this.#api = api;
		this.#reloadMs = reloadMs;
		this.#gapMs = gapMs;
		this.#clock = clock;
	}

	async load(): Promise<void> {
		if (this.#inflight !== null) {
			this.#again = true;
			return;
		}
		const controller = new AbortController();
		this.#inflight = controller;
		this.#lastLoad = this.#clock();
		try {
			const out = await call(this.#api.GET('/api/v1/planner/outlook', { signal: controller.signal }));
			if (!controller.signal.aborted) {
				this.outlook = out;
				this.error = null;
			}
		} catch (e) {
			if (!controller.signal.aborted && e instanceof ApiFailure) this.error = e.error;
		} finally {
			if (this.#inflight === controller) this.#inflight = null;
		}
		if (this.#again && !controller.signal.aborted) {
			this.#again = false;
			await this.load();
		}
	}

	start(): void {
		if (this.#started) return;
		this.#started = true;
		void this.load();
		this.#timer = setInterval(() => void this.load(), this.#reloadMs);
	}

	stop(): void {
		this.#started = false;
		if (this.#timer !== null) clearInterval(this.#timer);
		if (this.#deferred !== null) clearTimeout(this.#deferred);
		this.#timer = this.#deferred = null;
		this.#again = false;
		this.#inflight?.abort();
		this.#inflight = null;
	}

	onEvent(event: LiveEvent): void {
		if (!this.#started) return;
		if (event.type === 'decision' || event.type === 'scenario_run' || event.type === 'reset') {
			void this.load();
		} else if (event.type === 'state' && significant(event.data.changed as Record<string, unknown>)) {
			this.#stateChanged();
		}
	}

	#stateChanged(): void {
		if (this.#deferred !== null) return;
		const wait = this.#lastLoad + this.#gapMs - this.#clock();
		if (wait <= 0) {
			void this.load();
			return;
		}
		this.#deferred = setTimeout(() => {
			this.#deferred = null;
			void this.load();
		}, wait);
	}
}
