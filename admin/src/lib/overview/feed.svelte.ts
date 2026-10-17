import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { FeedItem, FeedWatermark, OverviewFeed } from '$lib/api/types';
import { FEED_TYPES, filtersKey, formatAfter, mergeFeed, NO_FILTERS, type FeedFilters } from './feed';

export const FEED_POLL_MS = 30_000;
const PAGE = 50;
// Приём порциями: приход больше этого за один опрос дочитается следующими.
const CATCH_UP = 100;
const MAX_CATCH_UP_CALLS = 50;

/** Лента событий аккаунтов (`GET /overview/feed`). Первая страница даёт знак приёма; опрос раз в
 * 30 с выбирает всё новее знака (`after`, повторяя, пока `has_more`) и вставляет записи по времени
 * события, загруженный хвост остаётся; «Загрузить ещё» идёт вниз по курсору. Один запрос в полёте
 * (опрос во время запроса - один после него), устаревший ответ не применяется; смена фильтров
 * начинает ленту заново. */
export class FeedStore {
	items = $state<FeedItem[]>([]);
	error = $state<ApiError | null>(null);
	/** Первая страница или опрос в полёте. */
	loading = $state(false);
	loadingMore = $state(false);
	filters = $state<FeedFilters>(NO_FILTERS);
	/** Курсор следующей (старшей) страницы; null - дальше ничего нет. */
	nextCursor = $state<string | null>(null);
	/** Первая страница получена хотя бы раз с текущими фильтрами. */
	loaded = $state(false);
	#api: Api;
	#watermark: FeedWatermark | null = null;
	#timer: ReturnType<typeof setInterval> | null = null;
	#inflight: AbortController | null = null;
	#again = false;
	#generation = 0;

	constructor(api: Api, filters: FeedFilters = NO_FILTERS) {
		this.#api = api;
		this.filters = filters;
	}

	get hasOlder(): boolean {
		return this.nextCursor !== null;
	}

	#query(extra: { cursor?: string; after?: string; limit: number }) {
		const { accountIds, types } = this.filters;
		const picked = FEED_TYPES.filter((t) => types.includes(t.type)).map((t) => t.type);
		return {
			...extra,
			...(accountIds.length ? { account_ids: [...new Set(accountIds)].sort((a, b) => a - b).join(',') } : {}),
			...(picked.length && picked.length < FEED_TYPES.length ? { types: picked.join(',') } : {})
		};
	}

	/** Начать ленту заново: первая страница с нынешними фильтрами, знак и курсор - от неё. */
	async load(): Promise<void> {
		this.#cancel();
		const controller = this.#begin();
		const generation = this.#generation;
		this.loading = true;
		try {
			const out = await call(
				this.#api.GET('/api/v1/overview/feed', { params: { query: this.#query({ limit: PAGE }) }, signal: controller.signal })
			);
			if (this.#current(controller, generation)) {
				this.items = mergeFeed([], out.items);
				this.#watermark = out.watermark;
				this.nextCursor = out.next_cursor;
				this.loaded = true;
				this.error = null;
			}
		} catch (e) {
			if (this.#current(controller, generation) && e instanceof ApiFailure) this.error = e.error;
		} finally {
			this.#end(controller);
		}
		await this.#rerun(controller, generation);
	}

	/** Записи, пришедшие после знака; пока первой страницы нет - она. */
	async poll(): Promise<void> {
		if (this.#inflight !== null) {
			this.#again = true;
			return;
		}
		if (this.#watermark === null) return this.load();
		const controller = this.#begin();
		const generation = this.#generation;
		this.loading = true;
		try {
			for (let calls = 0; calls < MAX_CATCH_UP_CALLS; calls++) {
				const mark: FeedWatermark | null = this.#watermark;
				if (mark === null) break;
				const out: OverviewFeed = await call(
					this.#api.GET('/api/v1/overview/feed', {
						params: { query: this.#query({ after: formatAfter(mark), limit: CATCH_UP }) },
						signal: controller.signal
					})
				);
				if (!this.#current(controller, generation)) return;
				this.items = mergeFeed(this.items, out.items);
				this.#watermark = out.watermark ?? mark;
				this.error = null;
				if (!out.has_more) break;
			}
		} catch (e) {
			if (this.#current(controller, generation) && e instanceof ApiFailure) this.error = e.error;
		} finally {
			this.#end(controller);
		}
		await this.#rerun(controller, generation);
	}

	/** Страница старше загруженного. */
	async loadMore(): Promise<void> {
		const cursor = this.nextCursor;
		if (cursor === null || this.#inflight !== null) return;
		const controller = this.#begin();
		const generation = this.#generation;
		this.loadingMore = true;
		try {
			const out = await call(
				this.#api.GET('/api/v1/overview/feed', {
					params: { query: this.#query({ cursor, limit: PAGE }) },
					signal: controller.signal
				})
			);
			if (this.#current(controller, generation)) {
				this.items = mergeFeed(this.items, out.items);
				this.nextCursor = out.next_cursor;
				this.error = null;
			}
		} catch (e) {
			if (this.#current(controller, generation) && e instanceof ApiFailure) this.error = e.error;
		} finally {
			this.#end(controller);
		}
		await this.#rerun(controller, generation);
	}

	setFilters(filters: FeedFilters): void {
		if (filtersKey(filters) === filtersKey(this.filters)) return;
		this.filters = filters;
		this.items = [];
		this.#watermark = null;
		this.nextCursor = null;
		this.loaded = false;
		this.error = null;
		this.#cancel();
		if (this.#timer !== null) void this.load();
	}

	start(): void {
		if (this.#timer !== null) return;
		void this.load();
		this.#timer = setInterval(() => void this.poll(), FEED_POLL_MS);
	}

	stop(): void {
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = null;
		this.#cancel();
	}

	#begin(): AbortController {
		const controller = new AbortController();
		this.#inflight = controller;
		return controller;
	}

	#end(controller: AbortController): void {
		if (this.#inflight === controller) {
			this.#inflight = null;
			this.loading = false;
			this.loadingMore = false;
		}
	}

	#current(controller: AbortController, generation: number): boolean {
		return !controller.signal.aborted && generation === this.#generation;
	}

	/** Опрос, пришедший во время запроса, выполняется один раз после него. */
	async #rerun(controller: AbortController, generation: number): Promise<void> {
		if (this.#again && this.#current(controller, generation)) {
			this.#again = false;
			await this.poll();
		}
	}

	#cancel(): void {
		this.#again = false;
		this.#generation += 1;
		this.#inflight?.abort();
		this.#inflight = null;
		this.loading = false;
		this.loadingMore = false;
	}
}
