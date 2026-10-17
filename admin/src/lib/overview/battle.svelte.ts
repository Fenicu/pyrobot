import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { BattleHq, BattleOrdersIn } from '$lib/api/types';

export const BATTLE_POLL_MS = 30_000;

/** Итог последнего сохранения: ошибка или кому доставлено уведомление о приказе. */
export interface SaveResult {
	battleAt: string;
	error: ApiError | null;
	woken: number[];
	notWoken: { account_id: number; reason: string }[];
}

/** «Штаб битвы» (`GET /overview/battle`): опрос раз в 30 с, один запрос в полёте (повтор во время
 * запроса — один после него), устаревший ответ не применяется. Запись приказов обгоняет чтение:
 * чтение в полёте отбрасывается, новое ждёт ответа записи — иначе устаревший ответ затёр бы
 * сохранённое. */
export class BattleStore {
	data = $state<BattleHq | null>(null);
	error = $state<ApiError | null>(null);
	loading = $state(false);
	saving = $state(false);
	result = $state<SaveResult | null>(null);
	/** Когда пришли показанные данные. */
	loadedAt = $state<Date | null>(null);
	#api: Api;
	#pollMs: number;
	#timer: ReturnType<typeof setInterval> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#generation = 0;

	constructor(api: Api, pollMs = BATTLE_POLL_MS) {
		this.#api = api;
		this.#pollMs = pollMs;
	}

	async load(): Promise<void> {
		if (this.#inflight !== null || this.saving) {
			this.#again = true;
			return;
		}
		const controller = new AbortController();
		const generation = this.#generation;
		this.#inflight = controller;
		this.loading = true;
		const current = () => !controller.signal.aborted && generation === this.#generation;
		try {
			const out = await call(this.#api.GET('/api/v1/overview/battle', { signal: controller.signal }));
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

	/** Заменить приказы на битву `battleAt` целиком. true - записано; ошибка и список разбуженных
	 * движков - в `result`. */
	async save(battleAt: string, body: BattleOrdersIn): Promise<boolean> {
		if (this.saving) return false;
		this.saving = true;
		this.result = null;
		this.#cancel();
		let ok = false;
		try {
			const { woken, not_woken, ...battle } = await call(
				this.#api.PUT('/api/v1/overview/battle/{battle_at}/orders', {
					params: { path: { battle_at: battleAt } },
					body
				})
			);
			const data = this.data;
			if (data !== null) {
				this.data = { ...data, battles: data.battles.map((b) => (b.battle_at === battle.battle_at ? battle : b)) };
			}
			this.result = { battleAt, error: null, woken, notWoken: not_woken };
			ok = true;
		} catch (e) {
			if (!(e instanceof ApiFailure)) throw e;
			this.result = { battleAt, error: e.error, woken: [], notWoken: [] };
		} finally {
			this.saving = false;
		}
		// Что бы ни вышло, показанное сверяется с сервером; с экрана уже ушли — не нужно.
		this.#again = false;
		if (this.#timer !== null) void this.load();
		return ok;
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
