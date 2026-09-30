import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { DailyOut } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

export const DAILY_RELOAD_MS = 60_000;
/** Кадры `state` идут пачками: итоги из-за них — не чаще раза в 30 с. */
export const DAILY_STATE_GAP_MS = 30_000;
const DAY_MS = 86_400_000;
const MSK_OFFSET_MS = 3 * 3_600_000;
/** Запас после полуночи: сервер уже считает новые сутки. */
const MIDNIGHT_SLACK_MS = 1_000;

/** Сколько до следующей полуночи по Москве (UTC+3 без перехода на летнее время). */
export function untilMskMidnight(nowMs: number): number {
	const local = nowMs + MSK_OFFSET_MS;
	return (Math.floor(local / DAY_MS) + 1) * DAY_MS - local;
}

/** «Итоги дня» (`GET /daily?days=N`): один запрос в полёте (повтор во время запроса — ещё один
 * после него). Перечитывание: при открытии, раз в минуту, на кадр `state` — не чаще раза в 30 с, в
 * полночь МСК (смена суток) и сразу после `reset` — новое поколение: запрос в полёте отменяется,
 * его поздний ответ не применяется, прежние итоги видны, пока не пришли новые. */
export class DailyStore {
	data = $state<DailyOut | null>(null);
	error = $state<ApiError | null>(null);
	loading = $state(false);
	/** Когда пришли показанные итоги: при ошибке обновления — время последней удачной загрузки. */
	loadedAt = $state<Date | null>(null);
	#api: AccountApi;
	#days: number;
	#reloadMs: number;
	#gapMs: number;
	#clock: () => number;
	#timer: ReturnType<typeof setInterval> | null = null;
	#midnight: ReturnType<typeof setTimeout> | null = null;
	#deferred: ReturnType<typeof setTimeout> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#started = false;
	#lastLoad = Number.NEGATIVE_INFINITY;
	#generation = 0;

	constructor(
		api: AccountApi,
		days: number,
		reloadMs = DAILY_RELOAD_MS,
		gapMs = DAILY_STATE_GAP_MS,
		clock: () => number = Date.now
	) {
		this.#api = api;
		this.#days = days;
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
		this.loading = true;
		const current = () => !controller.signal.aborted && generation === this.#generation;
		try {
			const out = await call(
				this.#api.GET('/daily', { params: { query: { days: this.#days } }, signal: controller.signal })
			);
			if (current()) {
				this.data = out;
				this.error = null;
				this.loadedAt = new Date(this.#clock());
			}
		} catch (e) {
			if (current() && e instanceof ApiFailure) this.error = e.error;
		} finally {
			if (this.#inflight === controller) {
				this.#inflight = null;
				this.loading = false;
			}
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
		this.#armMidnight();
	}

	stop(): void {
		this.#started = false;
		if (this.#timer !== null) clearInterval(this.#timer);
		if (this.#midnight !== null) clearTimeout(this.#midnight);
		this.#timer = null;
		this.#midnight = null;
		this.#cancel();
	}

	onEvent(event: LiveEvent): void {
		if (!this.#started) return;
		if (event.type === 'reset') {
			this.#cancel();
			this.error = null;
			void this.load();
		} else if (event.type === 'state') {
			this.#stateChanged();
		}
	}

	#armMidnight(): void {
		this.#midnight = setTimeout(() => {
			this.#midnight = null;
			if (!this.#started) return;
			void this.load();
			this.#armMidnight();
		}, untilMskMidnight(this.#clock()) + MIDNIGHT_SLACK_MS);
	}

	#cancel(): void {
		if (this.#deferred !== null) clearTimeout(this.#deferred);
		this.#deferred = null;
		this.#again = false;
		this.#generation += 1;
		this.#inflight?.abort();
		this.#inflight = null;
		this.loading = false;
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
