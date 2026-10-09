import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { GadgetsOut } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';

/** Поля состояния, от которых зависит карточка: надетые и рюкзак, запасы и шансы улучшений,
 * деньги и акции для плана покупки, уровень персонажа (тиры магазина). */
const STATE_KEYS = new Set([
	'gadgets',
	'bag',
	'bag_cap',
	'upgrades',
	'upgrade_info',
	'money',
	'level',
	'stock_holdings',
	'stock_quotes',
	'stock_limits'
]);

/** Блок «Гаджеты» (`GET /gadgets`): при открытии, на кадры `settings` (задача заточки —
 * секция настроек: старт, стоп, итог движка) и `reset`, на кадр `state` с полями карточки. Один
 * запрос в полёте: повтор во время запроса — ещё один после него. */
export class GadgetsStore {
	data = $state<GadgetsOut | null>(null);
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
			this.data = await call(this.#api.GET('/gadgets'));
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
	set(out: GadgetsOut): void {
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
