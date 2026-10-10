import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { AccountOut } from '$lib/api/types';
import { applyOrder } from '$lib/accounts/order';
import type { AccountOrderStore } from '$lib/accounts/order.svelte';

export const ACCOUNTS_POLL_MS = 30_000;

/** Аккаунты учётки со статусами. Общего потока событий нет: список перечитывается раз в 30 с и при
 * возвращении на вкладку. */
export class AccountsStore {
	#raw = $state<AccountOut[] | null>(null);
	#order: AccountOrderStore | undefined;
	#ordered = $derived.by(() => this.#raw && applyOrder(this.#raw, this.#order?.ids ?? null));
	/** Последняя ошибка чтения (список остаётся прежним). */
	error = $state<ApiError | null>(null);
	#api: Api;
	#onLoad: ((list: AccountOut[]) => void) | undefined;
	#timer: ReturnType<typeof setInterval> | null = null;
	// Номер чтения: ответ, который обогнало более новое чтение или `stop()`, не применяется.
	#request = 0;

	/** `onLoad` — после каждого применённого чтения списка; `order` — порядок пользователя. */
	constructor(api: Api, onLoad?: (list: AccountOut[]) => void, order?: AccountOrderStore) {
		this.#api = api;
		this.#onLoad = onLoad;
		this.#order = order;
	}

	/** null — ещё не загружен. В порядке пользователя, если он сохранён. */
	get list(): AccountOut[] | null {
		return this.#ordered;
	}

	set list(list: AccountOut[] | null) {
		this.#raw = list;
	}

	/** Новый порядок (id всего списка): сразу на экране, не сохранился — прежний и тост. */
	reorder(ids: number[]): Promise<boolean> {
		return this.#order?.save(ids) ?? Promise.resolve(false);
	}

	async load(): Promise<void> {
		const mine = ++this.#request;
		try {
			const list = await call(this.#api.GET('/api/v1/accounts'));
			if (mine !== this.#request) return;
			this.list = list;
			this.error = null;
			this.#onLoad?.(list);
		} catch (e) {
			if (mine === this.#request && e instanceof ApiFailure) this.error = e.error;
		}
	}

	#onVisibility = () => {
		if (document.visibilityState === 'visible') void this.load();
	};

	start(): void {
		if (this.#timer !== null) return;
		void this.load();
		this.#timer = setInterval(() => void this.load(), ACCOUNTS_POLL_MS);
		document.addEventListener('visibilitychange', this.#onVisibility);
	}

	/** Выход: опрос останавливается, список забывается. */
	stop(): void {
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = null;
		document.removeEventListener('visibilitychange', this.#onVisibility);
		this.#request += 1;
		this.list = null;
		this.error = null;
	}
}
