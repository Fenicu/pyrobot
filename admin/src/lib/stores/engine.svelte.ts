import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { EngineStatus } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

export const ENGINE_POLL_MS = 15_000;

/** Статус движка: отдельного события у него нет — опрос раз в 15 с, плюс кадр `settings`
 * (режим, пауза, kill) сразу. */
export class EngineStore {
	status = $state<EngineStatus | null>(null);
	/** Последняя ошибка опроса (503 — движок не запущен). */
	error = $state<ApiError | null>(null);
	#api: Api;
	#timer: ReturnType<typeof setInterval> | null = null;
	#pollMs: number;

	constructor(api: Api, pollMs = ENGINE_POLL_MS) {
		this.#api = api;
		this.#pollMs = pollMs;
	}

	async load(): Promise<void> {
		try {
			this.status = await call(this.#api.GET('/api/v1/engine/status'));
			this.error = null;
		} catch (e) {
			if (e instanceof ApiFailure) this.error = e.error;
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
	}

	onEvent(event: LiveEvent): void {
		if (event.type === 'settings') {
			const { mode, paused, killed } = event.data;
			if (this.status !== null && (mode === 'live' || mode === 'dry_run')) {
				this.status = { ...this.status, mode, paused, killed };
			}
			void this.load();
		} else if (event.type === 'reset') {
			void this.load();
		}
	}
}
