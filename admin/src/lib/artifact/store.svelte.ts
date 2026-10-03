import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { ArtifactOut } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

/** Поля состояния, от которых зависит карточка: уровни, идущий сбор, уровень персонажа (дела
 * Букваря). */
const STATE_KEYS = new Set(['artifacts', 'artifact_collect', 'level']);

/** «Сбор артефакта» (`GET /artifact`): при открытии, на кадры `settings` (запись сбора — секция
 * настроек: запуск, пауза, окончание движком) и `reset`, на кадр `state` с полями карточки. Один
 * запрос в полёте: повтор во время запроса — ещё один после него. */
export class ArtifactStore {
	data = $state<ArtifactOut | null>(null);
	error = $state<ApiError | null>(null);
	#api: AccountApi;
	#inflight = false;
	#again = false;
	#started = false;

	constructor(api: AccountApi) {
		this.#api = api;
	}

	async load(): Promise<void> {
		if (this.#inflight) {
			this.#again = true;
			return;
		}
		this.#inflight = true;
		try {
			this.data = await call(this.#api.GET('/artifact'));
			this.error = null;
		} catch (e) {
			if (e instanceof ApiFailure) this.error = e.error;
		} finally {
			this.#inflight = false;
		}
		if (this.#again) {
			this.#again = false;
			await this.load();
		}
	}

	/** Ответ действия — уже свежий вид. */
	set(out: ArtifactOut): void {
		this.data = out;
		this.error = null;
	}

	start(): void {
		if (this.#started) return;
		this.#started = true;
		void this.load();
	}

	stop(): void {
		this.#started = false;
	}

	onEvent(event: LiveEvent): void {
		if (!this.#started) return;
		if (event.type === 'settings' || event.type === 'reset') void this.load();
		else if (event.type === 'state' && Object.keys(event.data.changed).some((k) => STATE_KEYS.has(k))) void this.load();
	}
}
