import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { EngineStatus } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

export const ENGINE_POLL_MS = 15_000;
/** Опрос, пока включённый аккаунт ещё не запущен (старт процесса, «Включить»): плашка
 * «запускается» уходит сразу после старта, а не через 15 с. */
export const ENGINE_STARTING_POLL_MS = 2_000;

/** Статус движка: отдельного события у него нет — опрос раз в 15 с (пока включённый аккаунт не
 * запущен — раз в 2 с), плюс кадр `settings` (режим, пауза, kill) сразу. Без движка статус
 * отдаётся из базы с `running: false`. */
export class EngineStore {
	status = $state<EngineStatus | null>(null);
	/** Последняя ошибка опроса (сеть, сервер). */
	error = $state<ApiError | null>(null);
	#api: AccountApi;
	#timer: ReturnType<typeof setInterval> | null = null;
	#timerMs = 0;
	#active = false;
	#pollMs: number;

	constructor(api: AccountApi, pollMs = ENGINE_POLL_MS) {
		this.#api = api;
		this.#pollMs = pollMs;
	}

	async load(): Promise<void> {
		try {
			this.status = await call(this.#api.GET('/engine/status'));
			this.error = null;
		} catch (e) {
			if (e instanceof ApiFailure) this.error = e.error;
		}
		this.#schedule();
	}

	start(): void {
		if (this.#active) return;
		this.#active = true;
		this.#schedule();
		void this.load();
	}

	stop(): void {
		this.#active = false;
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = null;
	}

	/** Частота опроса — по последнему статусу; меняется только она — таймер заводится заново. */
	#schedule(): void {
		if (!this.#active) return;
		const starting = this.status?.status === 'enabled' && !this.status.running;
		const ms = starting ? ENGINE_STARTING_POLL_MS : this.#pollMs;
		if (this.#timer !== null && ms === this.#timerMs) return;
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timerMs = ms;
		this.#timer = setInterval(() => void this.load(), ms);
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
