import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { OverviewAccount } from '$lib/api/types';

export const OVERVIEW_POLL_MS = 30_000;

/** Сводка по аккаунтам (`GET /overview/accounts`): опрос раз в 30 с, один запрос в полёте (повтор
 * во время запроса - один после него), устаревший ответ не применяется. */
export class OverviewAccountsStore {
	data = $state<OverviewAccount[] | null>(null);
	error = $state<ApiError | null>(null);
	loading = $state(false);
	/** Когда пришли показанные данные. */
	loadedAt = $state<Date | null>(null);
	#api: Api;
	#pollMs: number;
	#timer: ReturnType<typeof setInterval> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#generation = 0;

	constructor(api: Api, pollMs = OVERVIEW_POLL_MS) {
		this.#api = api;
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
			const out = await call(this.#api.GET('/api/v1/overview/accounts', { signal: controller.signal }));
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
		this.#again = false;
		this.#generation += 1;
		this.#inflight?.abort();
		this.#inflight = null;
		this.loading = false;
	}
}
