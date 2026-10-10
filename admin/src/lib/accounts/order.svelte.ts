import { call, type Api } from '$lib/api/client';
import { toasts } from '$lib/stores/toasts.svelte';

const PATH = '/api/v1/me/ui/account-order';
export const SAVE_FAILED = 'Не удалось сохранить порядок аккаунтов';

interface Batch {
	ids: number[];
	done: ((ok: boolean) => void)[];
}

/** Порядок аккаунтов в списках у пользователя админки — один на телефоне и компьютере. */
export class AccountOrderStore {
	/** null — не сохранён или не прочитан: списки в порядке сервера. */
	ids = $state.raw<number[] | null>(null);
	#api: Api;
	#started = false;
	// Номер чтения: ответ после записи, `stop()` или более нового чтения не применяется.
	#request = 0;
	// Порядок, который точно есть на сервере: к нему откат, если последняя запись не прошла.
	#confirmed: number[] | null = null;
	// Одна запись в полёте, следом — только самый новый порядок: иначе параллельные PUT
	// могли бы прийти на сервер не по порядку и оставить там промежуточный.
	#sending = false;
	#next: Batch | null = null;
	// Поколение записей: после `stop()` завершение старой записи ничего не трогает.
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
			this.#confirmed = this.ids;
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
		this.#sending = false;
		const next = this.#next;
		this.#next = null;
		next?.done.forEach((done) => done(false));
		this.ids = null;
		this.#confirmed = null;
	}

	/** Новый порядок сразу на экране; не сохранился — последний сохранённый и тост.
	 * true — записан этот порядок или более новый, отправленный вместо него; false — не записан. */
	save(ids: number[]): Promise<boolean> {
		this.#request += 1;
		this.ids = ids;
		return new Promise((done) => {
			if (this.#sending) {
				this.#next = { ids, done: [...(this.#next?.done ?? []), done] };
			} else {
				void this.#send({ ids, done: [done] });
			}
		});
	}

	async #send(batch: Batch): Promise<void> {
		const gen = this.#write;
		this.#sending = true;
		let ok = true;
		try {
			await call(this.#api.PUT(PATH, { body: { version: 1, ids: batch.ids } }));
		} catch {
			ok = false;
		}
		batch.done.forEach((done) => done(ok));
		if (gen !== this.#write) return;
		if (ok) this.#confirmed = batch.ids;
		const next = this.#next;
		this.#next = null;
		this.#sending = false;
		if (next) {
			void this.#send(next);
		} else if (!ok) {
			this.ids = this.#confirmed;
			toasts.show(SAVE_FAILED, 'error');
		}
	}
}
