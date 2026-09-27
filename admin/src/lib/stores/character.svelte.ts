import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { PublicState } from '$lib/api/types';
import type { LiveEvent, SseState } from '$lib/live/sse';

export const STATE_RELOAD_MS = 60_000;

/** Состояние персонажа: `GET /state` при открытии, дальше `changed` из SSE по версии; `stale`
 * меняется и без событий, поэтому `/state` — ещё и раз в минуту. Снимок старше текущей версии
 * (ответ, обогнанный кадром SSE) не применяется; кадры, пришедшие до снимка или за разрывом
 * версий, ждут в очереди и применяются по порядку после синхронизации. */
export class CharacterStore {
	version = $state(0);
	state = $state<PublicState>({});
	stale = $state<string[]>([]);
	/** Время сервера последнего ответа. */
	now = $state<string | null>(null);
	loaded = $state(false);
	error = $state<ApiError | null>(null);
	#api: Api;
	#timer: ReturnType<typeof setInterval> | null = null;
	#reloadMs: number;
	#loading = false;
	#again = false;
	#queue: SseState[] = [];

	constructor(api: Api, reloadMs = STATE_RELOAD_MS) {
		this.#api = api;
		this.#reloadMs = reloadMs;
	}

	/** Синхронизация со снимком; идущая синхронизация не дублируется — повторится после неё. */
	async load(): Promise<void> {
		if (this.#loading) {
			this.#again = true;
			return;
		}
		this.#loading = true;
		let ok = false;
		try {
			const out = await call(this.#api.GET('/api/v1/state'));
			if (!this.loaded || out.version >= this.version) {
				this.version = out.version;
				this.state = out.state;
				this.stale = out.stale;
				this.loaded = true;
			}
			this.now = out.now;
			this.error = null;
			ok = true;
		} catch (e) {
			if (e instanceof ApiFailure) this.error = e.error;
		} finally {
			this.#loading = false;
		}
		// После неудачи разрыв не перезапускает синхронизацию сразу — повторит таймер или reset.
		this.#drain(ok);
		if (this.#again) {
			this.#again = false;
			await this.load();
		}
	}

	start(): void {
		if (this.#timer !== null) return;
		void this.load();
		this.#timer = setInterval(() => void this.load(), this.#reloadMs);
	}

	stop(): void {
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = null;
	}

	/** Кадр `state`: корневые поля из `changed` заменяются целиком. Старая версия — пропуск;
	 * до первого снимка или за разрывом — в очередь (разрыв запускает синхронизацию). */
	apply(update: SseState): void {
		if (this.loaded && update.version <= this.version) return;
		this.#queue.push(update);
		if (!this.loaded || this.#loading) return;
		this.#drain();
	}

	#drain(resync = true): void {
		if (!this.loaded) return;
		this.#queue.sort((a, b) => a.version - b.version);
		while (this.#queue.length > 0) {
			const next = this.#queue[0]!;
			if (next.version <= this.version) {
				this.#queue.shift();
			} else if (next.version === this.version + 1) {
				this.#queue.shift();
				this.state = { ...this.state, ...(next.changed as Partial<PublicState>) };
				this.version = next.version;
			} else {
				// Разрыв: пропущенные кадры восстанавливает свежий снимок.
				if (resync) void this.load();
				return;
			}
		}
	}

	isStale(field: string): boolean {
		return this.stale.includes(field);
	}

	onEvent(event: LiveEvent): void {
		if (event.type === 'state') this.apply(event.data);
		else if (event.type === 'reset') void this.load();
	}
}
