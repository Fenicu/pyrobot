import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { ScheduleOut } from '$lib/api/types';

export const SCHEDULE_POLL_MS = 30_000;

/** «Расписание» (`GET /overview/schedule`): опрос каждые 30 с, один запрос в полёте
 * (повтор во время запроса — один после него), устаревший ответ отбрасывается.
 * Опрос останавливается при уходе с экрана. */
export class ScheduleStore {
	data = $state<ScheduleOut | null>(null);
	error = $state<ApiError | null>(null);
	loading = $state(false);
	loadedAt = $state<Date | null>(null);
	#api: Api;
	#hours: number;
	#pollMs: number;
	#timer: ReturnType<typeof setInterval> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#generation = 0;

	constructor(api: Api, hours = 12, pollMs = SCHEDULE_POLL_MS) {
		this.#api = api;
		this.#hours = hours;
		this.#pollMs = pollMs;
	}

	async load(): Promise<void> {
		if (this.#inflight !== null) {
			this.#again = true;
			return;
		}
		const controller = new AbortController();
		const generation = this.#generation;
		this.#inflight = controller;
		this.loading = true;
		const current = () => !controller.signal.aborted && generation === this.#generation;
		try {
			const out = await call(
				this.#api.GET('/api/v1/overview/schedule', {
					params: { query: { hours: this.#hours } },
					signal: controller.signal
				})
			);
			if (current()) {
				this.data = out;
				this.error = null;
				this.loadedAt = new Date();
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
		if (this.#timer !== null) return;
		void this.load();
		this.#timer = setInterval(() => void this.load(), this.#pollMs);
	}

	stop(): void {
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = null;
		this.#cancel();
	}

	#cancel(): void {
		this.#again = false;
		this.#generation += 1;
		this.#inflight?.abort();
		this.#inflight = null;
		this.loading = false;
	}
}
