import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { BlockId } from './blocks';
import { copyLayout, DEFAULT_LAYOUT, DEFAULT_SIZE, firstFree, normalizeLayout, sameLayout, type HomeLayout } from './layout';

const PATH = '/api/v1/me/ui/home-layout';

/** Раскладка главной пользователя админки (одна на все его аккаунты) и её правка в «Настроить». */
export class HomeLayoutStore {
	/** Раскладка на экране; до загрузки — по умолчанию. */
	layout = $state.raw<HomeLayout>(copyLayout(DEFAULT_LAYOUT));
	/** Черновик режима «Настроить»; null — режим выключен. */
	draft = $state.raw<HomeLayout | null>(null);
	editing = $derived(this.draft !== null);
	dirty = $derived(this.draft !== null && !sameLayout(this.draft, this.layout));
	saving = $state(false);
	error = $state.raw<ApiError | null>(null);
	#api: Api;
	#started = false;
	// Номер чтения: ответ после `stop()` или более нового чтения не применяется.
	#request = 0;

	constructor(api: Api) {
		this.#api = api;
	}

	async load(): Promise<void> {
		const mine = ++this.#request;
		try {
			const out = await call(this.#api.GET(PATH));
			if (mine !== this.#request) return;
			this.layout = normalizeLayout(out.layout ?? DEFAULT_LAYOUT);
			this.error = null;
		} catch (e) {
			if (mine === this.#request && e instanceof ApiFailure) this.error = e.error;
		}
	}

	/** После входа: прочитать один раз. */
	start(): void {
		if (this.#started) return;
		this.#started = true;
		void this.load();
	}

	/** Выход: забыть раскладку и черновик. */
	stop(): void {
		this.#started = false;
		this.#request += 1;
		this.layout = copyLayout(DEFAULT_LAYOUT);
		this.draft = null;
		this.error = null;
	}

	begin(): void {
		if (this.draft === null) this.draft = copyLayout(this.layout);
	}

	cancel(): void {
		this.draft = null;
	}

	/** Раскладка из сетки. Вне правки — только поправка того, что на экране (сетка раздвинула
	 * наложения), без записи на сервер. */
	update(l: HomeLayout): void {
		if (this.draft !== null) this.draft = l;
		else this.layout = l;
	}

	hide(id: BlockId): void {
		const d = this.draft;
		if (d === null || d.hidden.includes(id)) return;
		this.draft = { version: 1, items: d.items.filter((i) => i.id !== id), hidden: [...d.hidden, id] };
	}

	restore(id: BlockId): void {
		const d = this.draft;
		if (d === null || !d.hidden.includes(id)) return;
		const { w, h } = DEFAULT_SIZE[id];
		this.draft = {
			version: 1,
			items: [...d.items, { id, ...firstFree(d.items, w, h), w, h }],
			hidden: d.hidden.filter((x) => x !== id)
		};
	}

	reset(): void {
		if (this.draft !== null) this.draft = copyLayout(DEFAULT_LAYOUT);
	}

	/** «Готово»: true — сохранено (или нечего сохранять) и режим правки закрыт. */
	async save(): Promise<boolean> {
		const d = this.draft;
		if (d === null) return true;
		if (!this.dirty) {
			this.draft = null;
			return true;
		}
		this.saving = true;
		try {
			await call(this.#api.PUT(PATH, { body: d }));
			this.layout = d;
			if (this.draft === d) this.draft = null;
			this.error = null;
			return true;
		} catch (e) {
			this.error = e instanceof ApiFailure ? e.error : { kind: 'network', status: 0, message: String(e) };
			return false;
		} finally {
			this.saving = false;
		}
	}
}
