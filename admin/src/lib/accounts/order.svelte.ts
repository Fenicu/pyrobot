import { call, type Api } from '$lib/api/client';
import { toasts } from '$lib/stores/toasts.svelte';

const PATH = '/api/v1/me/ui/account-order';
export const SAVE_FAILED = 'Не удалось сохранить порядок аккаунтов';

/** Порядок аккаунтов в списках у пользователя админки — один на телефоне и компьютере. */
export class AccountOrderStore {
	/** null — не сохранён или не прочитан: списки в порядке сервера. */
	ids = $state.raw<number[] | null>(null);
	#api: Api;
	#started = false;
	// Номер чтения: ответ после записи, `stop()` или более нового чтения не применяется.
	#request = 0;
	// Номер записи: откатывает только последняя, иначе упавшая старая затёрла бы новую.
	#write = 0;

	constructor(api: Api) {
		this.#api = api;
	}

	/** Ошибка чтения не показывается: остаётся порядок по умолчанию. */
	async load(): Promise<void> {
		const mine = ++this.#request;
		try {
			const out = await call(this.#api.GET(PATH));
			if (mine !== this.#request) return;
			const ids = out.order?.ids;
			this.ids = Array.isArray(ids) ? ids.filter((id) => Number.isInteger(id)) : null;
		} catch {
			// порядок по умолчанию
		}
	}

	start(): void {
		if (this.#started) return;
		this.#started = true;
		void this.load();
	}

	stop(): void {
		this.#started = false;
		this.#request += 1;
		this.#write += 1;
		this.ids = null;
	}

	/** Новый порядок сразу на экране; не сохранился — прежний и тост. */
	async save(ids: number[]): Promise<boolean> {
		const prev = this.ids;
		const mine = ++this.#write;
		this.#request += 1;
		this.ids = ids;
		try {
			await call(this.#api.PUT(PATH, { body: { version: 1, ids } }));
			return true;
		} catch {
			if (mine === this.#write) {
				this.ids = prev;
				toasts.show(SAVE_FAILED, 'error');
			}
			return false;
		}
	}
}
