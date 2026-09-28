import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { Outlook } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

export const PLAN_RELOAD_MS = 60_000;
/** Кадры `state` идут пачками: план из-за них — не чаще раза в 5 с (и кеш сервера — 5 с). */
export const PLAN_STATE_GAP_MS = 5_000;

/** Поля состояния, которых планировщик не читает (`app/engine/planner/**`; сверку держит
 * `tests/test_plan_ignored_fields.py`): их изменение план не меняет. Любое другое поле — повод
 * перечитать план. */
export const IGNORED = new Set([
	'schema_version',
	'exp',
	'exp_next',
	'motivation_max',
	'bag',
	'bag_cap',
	'tangerines',
	'skills',
	'woke_at',
	'upgrades',
	'last_refusal',
	'team_tag',
	'factory_wins',
	'factory_call_at',
	'stock_holdings'
]);

export function significant(changed: Record<string, unknown>): boolean {
	return Object.keys(changed).some((k) => !IGNORED.has(k));
}

/** «План бота» (`GET /planner/outlook`): один запрос в полёте на вкладку (повтор во время запроса —
 * ещё один после него), отмена при уходе с главной. Перечитывание: при открытии, на кадр
 * `decision` и `scenario_run`, на кадр `state` с полями, которые читает планировщик, — не чаще раза
 * в 5 с, раз в минуту. `reset` — новое поколение: план очищается, запрос в полёте отменяется, его
 * поздний ответ не применяется. */
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
	#generation = 0;

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
		const generation = this.#generation;
		this.#inflight = controller;
		this.#lastLoad = this.#clock();
		// Ответ прежнего поколения (до reset или ухода с главной) не применяется.
		const current = () => !controller.signal.aborted && generation === this.#generation;
		try {
			const out = await call(this.#api.GET('/api/v1/planner/outlook', { signal: controller.signal }));
			if (current()) {
				this.outlook = out;
				this.error = null;
			}
		} catch (e) {
			if (current() && e instanceof ApiFailure) this.error = e.error;
		} finally {
			if (this.#inflight === controller) this.#inflight = null;
		}
		if (this.#again && current()) {
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
		this.#timer = null;
		this.#cancel();
	}

	/** Отменить запрос в полёте и отложенное перечитывание; начать новое поколение ответов. */
	#cancel(): void {
		if (this.#deferred !== null) clearTimeout(this.#deferred);
		this.#deferred = null;
		this.#again = false;
		this.#generation += 1;
		this.#inflight?.abort();
		this.#inflight = null;
	}

	onEvent(event: LiveEvent): void {
		if (!this.#started) return;
		if (event.type === 'reset') {
			// Поток начался заново: прежний план мог быть снят со старого снимка.
			this.#cancel();
			this.outlook = null;
			this.error = null;
			void this.load();
		} else if (event.type === 'decision' || event.type === 'scenario_run') {
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
