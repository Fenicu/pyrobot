import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { OverviewTotals } from '$lib/api/types';
import { untilMskMidnight } from '$lib/daily/store.svelte';
import { pollIntervalMs, type Period } from './totals';

/** Запас после полуночи: сервер уже считает новые сутки. */
const MIDNIGHT_SLACK_MS = 1_000;

/** Итоги по аккаунтам (`GET /overview/totals?days=`): опрос по периоду («Сегодня» раз в 30 с, 7 и
 * 30 дней раз в 5 минут), перечитывание на возвращение во вкладку и в полночь МСК. Один запрос в
 * полёте (повтор во время запроса - один после него), устаревший ответ не применяется; смена периода
 * отменяет запрос в полёте и сбрасывает показанное (чужой период нельзя выдавать за свой). */
export class TotalsStore {
	data = $state<OverviewTotals | null>(null);
	error = $state<ApiError | null>(null);
	loading = $state(false);
	days = $state<Period>(7);
	/** Когда пришли показанные данные. */
	loadedAt = $state<Date | null>(null);
	#api: Api;
	#clock: () => number;
	#timer: ReturnType<typeof setInterval> | null = null;
	#midnight: ReturnType<typeof setTimeout> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#started = false;
	#generation = 0;

	constructor(api: Api, days: Period, clock: () => number = Date.now) {
		this.#api = api;
		this.days = days;
		this.#clock = clock;
	}

	async load(): Promise<void> {
		if (this.#inflight !== null) {
			this.#again = true;
			return;
		}
		const controller = new AbortController();
		const generation = this.#generation;
		const days = this.days;
		this.#inflight = controller;
		this.loading = true;
		const current = () => !controller.signal.aborted && generation === this.#generation;
		try {
			const out = await call(this.#api.GET('/api/v1/overview/totals', { params: { query: { days } }, signal: controller.signal }));
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

	setDays(days: Period): void {
		if (days === this.days) return;
		this.days = days;
		this.data = null;
		this.error = null;
		this.loadedAt = null;
		this.#cancel();
		if (this.#started) {
			this.#armPoll();
			void this.load();
		}
	}

	start(): void {
		if (this.#started) return;
		this.#started = true;
		void this.load();
		this.#armPoll();
		this.#armMidnight();
		document.addEventListener('visibilitychange', this.#onVisible);
	}

	stop(): void {
		this.#started = false;
		if (this.#timer !== null) clearInterval(this.#timer);
		if (this.#midnight !== null) clearTimeout(this.#midnight);
		this.#timer = null;
		this.#midnight = null;
		document.removeEventListener('visibilitychange', this.#onVisible);
		this.#cancel();
	}

	#onVisible = (): void => {
		if (document.visibilityState !== 'hidden') void this.load();
	};

	#armPoll(): void {
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = setInterval(() => void this.load(), pollIntervalMs(this.days));
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
		this.#again = false;
		this.#generation += 1;
		this.#inflight?.abort();
		this.#inflight = null;
		this.loading = false;
	}
}
