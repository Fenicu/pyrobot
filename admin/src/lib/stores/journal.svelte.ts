import { call, type Api } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { ActionItem, ActionOut, DecisionItem, JournalItem, MessageItem } from '$lib/api/types';
import { isActionCreated, type LiveEvent } from '$lib/live/sse';

export type FeedType = JournalItem['type'];
export const FEED_PAGE = 50;

export interface FeedFilter {
	/** null — все типы. */
	type: FeedType | null;
	/** Статус действия (сужает ленту до действий). */
	status: string | null;
	/** Источник действия (сужает ленту до действий). */
	source: string | null;
	/** Дни по МСК, включительно: YYYY-MM-DD. */
	since: string | null;
	until: string | null;
}

export const EMPTY_FILTER: FeedFilter = { type: null, status: null, source: null, since: null, until: null };

export const keyOf = (item: JournalItem) => `${item.type}:${item.id}`;

function mskMidnight(day: string, plusDays = 0): string {
	const d = new Date(`${day}T00:00:00+03:00`);
	d.setUTCDate(d.getUTCDate() + plusDays);
	return d.toISOString();
}

/** Параметры `GET /journal` по фильтру. */
export function feedQuery(f: FeedFilter, cursor: string | null) {
	const actionsOnly = f.status !== null || f.source !== null;
	const type = actionsOnly ? 'action' : f.type;
	return {
		...(type ? { types: type } : {}),
		...(f.status ? { status: f.status } : {}),
		...(f.source ? { source: f.source } : {}),
		...(f.since ? { since: mskMidnight(f.since) } : {}),
		...(f.until ? { until: mskMidnight(f.until, 1) } : {}),
		...(cursor ? { cursor } : {}),
		limit: FEED_PAGE
	};
}

/** Живой элемент из кадра SSE в форме элемента /journal. */
export function liveItem(event: LiveEvent, receivedAt: string): JournalItem | null {
	switch (event.type) {
		case 'message': {
			const d = event.data;
			const item: MessageItem = {
				type: 'message',
				id: d.journal_id,
				at: receivedAt,
				chat_id: d.chat_id,
				msg_id: d.msg_id,
				revision: d.revision,
				kind: d.kind,
				date: d.date,
				outgoing: d.outgoing,
				recovered: false,
				text: d.text,
				markup: d.markup as MessageItem['markup'],
				events: d.events
			};
			return item;
		}
		case 'action': {
			const d = event.data;
			if (!isActionCreated(d)) return null;
			const item: ActionItem = {
				type: 'action',
				id: d.id,
				// В кадре создания нет момента — момент получения.
				at: receivedAt,
				source: d.source,
				kind: d.kind,
				chat_id: d.chat_id,
				command_class: d.command_class,
				status: d.status,
				reason: d.reason,
				text: d.text,
				data: d.data,
				message_id: d.message_id ?? null,
				chat_title: d.chat_title ?? null,
				finished_at: null,
				run_id: d.scenario_run_id ?? null
			};
			return item;
		}
		case 'decision': {
			// Запуск решения появится позже — кадром scenario_run с decision_id.
			const item: DecisionItem = { type: 'decision', ...event.data, run_id: null };
			return item;
		}
		default:
			return null;
	}
}

/** Действие из `GET /actions/{id}` в форме элемента ленты. */
export function actionItem(a: ActionOut): ActionItem {
	const text = a.payload.text;
	const data = a.payload.data;
	const messageId = a.payload.message_id;
	const chatTitle = a.payload.chat_title;
	return {
		type: 'action',
		id: a.id,
		at: a.created_at,
		source: a.source,
		kind: a.kind,
		chat_id: a.chat_id,
		command_class: a.command_class,
		status: a.status,
		reason: a.reason,
		text: typeof text === 'string' ? text : null,
		data: typeof data === 'string' ? data : null,
		message_id: typeof messageId === 'number' ? messageId : null,
		chat_title: typeof chatTitle === 'string' ? chatTitle : null,
		finished_at: a.finished_at,
		run_id: a.scenario_run_id
	};
}

function matches(item: JournalItem, f: FeedFilter): boolean {
	const at = new Date(item.at).getTime();
	if (f.since !== null && at < new Date(mskMidnight(f.since)).getTime()) return false;
	if (f.until !== null && at >= new Date(mskMidnight(f.until, 1)).getTime()) return false;
	if (f.status !== null || f.source !== null) {
		if (item.type !== 'action') return false;
		if (f.status !== null && item.status !== f.status) return false;
		if (f.source !== null && item.source !== f.source) return false;
		return true;
	}
	return f.type === null || item.type === f.type;
}

const SOURCES_KEPT = 500;
/** Живых строк сверху не больше: старые отбрасываются (страницы снизу и курсор не трогаются). */
export const LIVE_KEPT = 500;
const newer = (a: JournalItem, b: JournalItem) => new Date(a.at).getTime() > new Date(b.at).getTime();

/** Лента журнала: страницы по курсору снизу, живые элементы из SSE сверху (ключ — тип + id),
 * обновления действия меняют существующую строку. Кадры, пришедшие во время загрузки первой
 * страницы, после её прихода применяются заново; действие, которое прошло фильтр только после
 * обновления статуса, дочитывается из `GET /actions/{id}`. */
export class JournalFeed {
	items = $state.raw<JournalItem[]>([]);
	filter = $state<FeedFilter>({ ...EMPTY_FILTER });
	cursor = $state<string | null>(null);
	loading = $state(false);
	error = $state<ApiError | null>(null);
	/** Живое из SSE включено. */
	live = $state(true);
	/** Пришло, пока live выключен. */
	missed = $state(0);
	#api: Api;
	#seq = 0;
	/** Изменения ленты, сделанные во время загрузки первой страницы: повторяются поверх неё. */
	#during: (() => void)[] | null = null;
	/** Действия, которые уже дочитываются. */
	#fetching = new Set<number>();
	/** Источник действий по кадрам создания (последние SOURCES_KEPT). */
	#sources = new Map<number, string>();
	/** Ключи всех строк ленты. */
	#keys = new Set<string>();
	/** Ключи живых строк в порядке прихода (не из страниц). */
	#live = new Set<string>();

	constructor(api: Api) {
		this.#api = api;
	}

	get done(): boolean {
		return this.cursor === null;
	}

	async reload(): Promise<void> {
		this.cursor = null;
		this.missed = 0;
		await this.#fetch(true);
	}

	async more(): Promise<void> {
		if (this.cursor === null || this.loading) return;
		await this.#fetch(false);
	}

	setFilter(next: Partial<FeedFilter>): void {
		this.filter = { ...this.filter, ...next };
		void this.reload();
	}

	onEvent(event: LiveEvent, now = new Date()): void {
		if (event.type === 'reset') {
			void this.reload();
			return;
		}
		if (event.type === 'action' && !isActionCreated(event.data)) {
			const { id, status, reason } = event.data;
			this.#change(() => this.#update(id, status, reason));
			if (!this.#has(`action:${id}`) && this.#mayMatch(id, status)) void this.#fetchAction(id);
			return;
		}
		if (event.type === 'scenario_run') {
			const { id, decision_id } = event.data;
			if (decision_id != null) this.#change(() => this.#linkRun(decision_id, id));
			return;
		}
		if (event.type === 'action' && isActionCreated(event.data)) {
			this.#remember(event.data.id, event.data.source);
		}
		const item = liveItem(event, now.toISOString());
		if (item === null || !matches(item, this.filter)) return;
		if (!this.live) {
			this.missed += 1;
			return;
		}
		this.#change(() => this.#prepend(item));
	}

	/** Отсутствующее действие после обновления может пройти фильтр статуса или источника (у
	 * источника — если кадр создания не показал другой). */
	#mayMatch(id: number, status: string): boolean {
		const f = this.filter;
		if (f.status === null && f.source === null) return false;
		if (f.status !== null && status !== f.status) return false;
		const source = this.#sources.get(id);
		return f.source === null || source === undefined || source === f.source;
	}

	#remember(id: number, source: string): void {
		this.#sources.set(id, source);
		if (this.#sources.size > SOURCES_KEPT) {
			const oldest = this.#sources.keys().next().value;
			if (oldest !== undefined) this.#sources.delete(oldest);
		}
	}

	#has(key: string): boolean {
		return this.#keys.has(key);
	}

	#setItems(items: JournalItem[]): void {
		this.items = items;
		this.#keys = new Set(items.map(keyOf));
		this.#live.clear();
	}

	/** Живая строка добавлена: самая старая сверх LIVE_KEPT уходит из ленты. */
	#added(key: string): void {
		this.#keys.add(key);
		this.#live.add(key);
		if (this.#live.size <= LIVE_KEPT) return;
		const oldest = this.#live.values().next().value;
		if (oldest === undefined) return;
		this.#live.delete(oldest);
		this.#keys.delete(oldest);
		this.items = this.items.filter((it) => keyOf(it) !== oldest);
	}

	/** Изменение применяется сразу; во время загрузки первой страницы — ещё и после неё. */
	#change(op: () => void): void {
		op();
		this.#during?.push(op);
	}

	#update(id: number, status: string, reason: string): void {
		const i = this.items.findIndex((it) => it.type === 'action' && it.id === id);
		const row = this.items[i];
		if (row?.type === 'action') this.items = this.items.with(i, { ...row, status, reason: reason || row.reason });
	}

	/** Запуск начат решением (кадр scenario_run): решение и шаги запуска — одна строка хроники. */
	#linkRun(decisionId: number, runId: number): void {
		const i = this.items.findIndex((it) => it.type === 'decision' && it.id === decisionId);
		const row = this.items[i];
		if (row?.type === 'decision' && row.run_id === null) this.items = this.items.with(i, { ...row, run_id: runId });
	}

	#prepend(item: JournalItem): void {
		if (this.#has(keyOf(item))) return;
		this.items = [item, ...this.items];
		this.#added(keyOf(item));
	}

	/** Вставка по моменту: лента идёт от новых к старым. */
	#insert(item: JournalItem): void {
		if (this.#has(keyOf(item))) return;
		const i = this.items.findIndex((it) => newer(item, it));
		this.items = i === -1 ? [...this.items, item] : [...this.items.slice(0, i), item, ...this.items.slice(i)];
		this.#added(keyOf(item));
	}

	async #fetchAction(id: number): Promise<void> {
		if (this.#fetching.has(id)) return;
		this.#fetching.add(id);
		const filter = this.filter;
		try {
			const action = await call(
				this.#api.GET('/api/v1/actions/{action_id}', { params: { path: { action_id: id } } })
			);
			const item = actionItem(action);
			if (this.filter !== filter || !matches(item, filter)) return;
			if (!this.live) {
				this.missed += 1;
				return;
			}
			this.#change(() => this.#insert(item));
		} catch {
			// Строка появится при следующем перечитывании.
		} finally {
			this.#fetching.delete(id);
		}
	}

	async #fetch(first: boolean): Promise<void> {
		const seq = ++this.#seq;
		this.loading = true;
		if (first) this.#during = [];
		const during = this.#during;
		try {
			const page = await call(
				this.#api.GET('/api/v1/journal', {
					params: { query: feedQuery(this.filter, first ? null : this.cursor) }
				})
			);
			if (seq !== this.#seq) return;
			if (first) {
				this.#setItems(page.items);
				for (const op of during ?? []) op();
			} else {
				const fresh = page.items.filter((it) => !this.#keys.has(keyOf(it)));
				this.items = [...this.items, ...fresh];
				for (const it of fresh) this.#keys.add(keyOf(it));
			}
			this.cursor = page.next_cursor;
			this.error = null;
		} catch (e) {
			if (seq === this.#seq && e instanceof ApiFailure) this.error = e.error;
		} finally {
			if (seq === this.#seq) {
				this.loading = false;
				if (first) this.#during = null;
			}
		}
	}
}
