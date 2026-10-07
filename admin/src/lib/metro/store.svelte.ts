import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import type { MetroLive } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';
import { supersedes, withHistory } from './live';

/** Живой кадр забега метро для главной: `GET /metro/live` при открытии и на `reset`, дальше — кадры
 * `metro_live` потока. Кадр применяется, только если он новее показанного (`supersedes`): поздний
 * ответ GET не откатывает кадр потока. 204 — забега с запуска движка не было: кадр прошлого запуска
 * снимается. Ошибки (движок не запущен и т. п.) кадр не меняют — это видно на главной и без карточки. */
export class MetroLiveStore {
	frame = $state<MetroLive | null>(null);
	/** Когда показанный кадр получен (мс): замершие кадры и возраст итога. */
	receivedAt = $state<number | null>(null);
	/** Идущий запуск сценария метро по кадрам `scenario_run` (null — не видно): итог прошлого забега
	 * отличается от входа в новый. */
	metroRunId = $state<number | null>(null);
	#api: AccountApi;
	#clock: () => number;
	#inflight: AbortController | null = null;
	#started = false;

	constructor(api: AccountApi, clock: () => number = Date.now) {
		this.#api = api;
		this.#clock = clock;
	}

	async load(): Promise<void> {
		this.#inflight?.abort();
		const controller = new AbortController();
		this.#inflight = controller;
		const asked = this.#clock();
		try {
			const frame = await call(this.#api.GET('/metro/live', { signal: controller.signal }));
			if (controller.signal.aborted) return;
			if (frame) this.#apply(frame);
			// 204 — у движка кадра нет (перезапуск): прежний кадр устарел, если не пришёл после запроса.
			else if (this.receivedAt === null || this.receivedAt <= asked) this.frame = this.receivedAt = null;
		} catch {
			// Кадра нет — карточки нет.
		} finally {
			if (this.#inflight === controller) this.#inflight = null;
		}
	}

	start(): void {
		if (this.#started) return;
		this.#started = true;
		void this.load();
	}

	stop(): void {
		this.#started = false;
		this.#inflight?.abort();
		this.#inflight = null;
	}

	onEvent(event: LiveEvent): void {
		if (!this.#started) return;
		if (event.type === 'reset') void this.load();
		else if (event.type === 'metro_live') this.#apply(event.data);
		else if (event.type === 'scenario_run') {
			const run = event.data;
			if (run.scenario === 'metro' && run.status === 'running') this.metroRunId = run.id;
			else if (run.id === this.metroRunId && run.status !== 'running') this.metroRunId = null;
		}
	}

	#apply(next: MetroLive): void {
		if (!supersedes(next, this.frame)) return;
		this.frame = withHistory(next, this.frame);
		this.receivedAt = this.#clock();
	}
}
