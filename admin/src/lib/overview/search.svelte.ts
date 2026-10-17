import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { SearchOut } from '$lib/api/types';
import { validateQuery, type SearchQuery } from './search';

/** Состояние и запросы поиска по сообщениям (`GET /api/v1/overview/search`). */
export class SearchStore {
	q = $state('');
	where = $state<'db' | 'telegram'>('db');
	accountIds = $state<number[]>([]);
	limit = $state(50);

	result = $state<SearchOut | null>(null);
	error = $state<ApiError | null>(null);
	validationError = $state<string | null>(null);
	loading = $state(false);
	viewMode = $state<'grouped' | 'flat'>('grouped');

	#api: Api;
	#controller: AbortController | null = null;
	#generation = 0;

	constructor(api: Api, initial?: Partial<SearchQuery>) {
		this.#api = api;
		if (initial?.q !== undefined) this.q = initial.q;
		if (initial?.where !== undefined) this.where = initial.where;
		if (initial?.accountIds !== undefined) this.accountIds = initial.accountIds;
		if (initial?.limit !== undefined) this.limit = initial.limit;
	}

	get query(): SearchQuery {
		return {
			q: this.q,
			where: this.where,
			accountIds: this.accountIds,
			limit: this.limit
		};
	}

	setViewMode(mode: 'grouped' | 'flat'): void {
		this.viewMode = mode;
	}

	/** Прервать текущий запрос. */
	abort(): void {
		if (this.#controller !== null) {
			this.#controller.abort();
			this.#controller = null;
		}
	}

	/** Запустить поиск: отменяет прежний запрос и валидирует длину строки. */
	async search(override?: Partial<SearchQuery>): Promise<void> {
		if (override?.q !== undefined) this.q = override.q;
		if (override?.where !== undefined) this.where = override.where;
		if (override?.accountIds !== undefined) this.accountIds = override.accountIds;
		if (override?.limit !== undefined) this.limit = override.limit;

		const validation = validateQuery(this.q);
		if (validation !== null) {
			this.validationError = validation;
			return;
		}
		this.validationError = null;

		this.abort();
		const controller = new AbortController();
		this.#controller = controller;
		const generation = ++this.#generation;
		this.loading = true;
		this.error = null;

		try {
			const queryParams: { q: string; where: 'db' | 'telegram'; limit: number; account_ids?: string } = {
				q: this.q.trim(),
				where: this.where,
				limit: this.limit
			};
			if (this.accountIds.length > 0) {
				queryParams.account_ids = [...new Set(this.accountIds)].sort((a, b) => a - b).join(',');
			}
			const out = await call(
				this.#api.GET('/api/v1/overview/search', {
					params: { query: queryParams },
					signal: controller.signal
				})
			);
			if (this.#generation === generation) {
				this.result = out;
				this.loading = false;
				this.error = null;
			}
		} catch (e) {
			if (controller.signal.aborted) return;
			if (this.#generation === generation) {
				this.loading = false;
				if (e instanceof ApiFailure) {
					this.error = e.error;
				} else {
					this.error = { kind: 'network', status: 0, message: e instanceof Error ? e.message : String(e) };
				}
			}
		} finally {
			if (this.#controller === controller) {
				this.#controller = null;
			}
		}
	}
}
