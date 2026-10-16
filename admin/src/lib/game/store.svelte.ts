import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure, type ApiError } from '$lib/api/errors';
import type { MessageItem, SettingsOut } from '$lib/api/types';
import type { LiveEvent } from '$lib/live/sse';
import { fromItem, fromLive, merge, upsert, type ChatMessage } from './chat';

export const CHAT_PAGE = 50;

export interface ChatNote {
	id: number;
	/** Стоит после сообщения с этим msg_id (последнего на момент появления). */
	after: number;
	text: string;
	kind: 'ok' | 'warn' | 'error' | 'info';
}

export type ChatEntry =
	| { type: 'message'; key: string; message: ChatMessage }
	| { type: 'note'; key: string; note: ChatNote };

/** Чат игры из настроек аккаунта: своё значение, иначе значение по умолчанию. */
function gameChatId(settings: SettingsOut): number | null {
	for (const source of [settings.values, settings.defaults]) {
		const chats = source.chats as { game_chat_id?: unknown } | undefined;
		if (typeof chats?.game_chat_id === 'number') return chats.game_chat_id;
	}
	return null;
}

/** Чат с игрой: последние сообщения журнала, более ранние — страницами, новые и правки — из
 * потока. Системные строки (отказы команд) живут только на экране. */
export class GameChat {
	chatId = $state<number | null>(null);
	messages = $state.raw<ChatMessage[]>([]);
	notes = $state.raw<ChatNote[]>([]);
	loaded = $state(false);
	loading = $state(false);
	loadingOlder = $state(false);
	error = $state<ApiError | null>(null);
	olderError = $state<ApiError | null>(null);
	#cursor = $state<string | null>(null);
	#api: AccountApi;
	#generation = 0;
	#noteSeq = 0;
	#pending: Promise<void> = Promise.resolve();

	constructor(api: AccountApi) {
		this.#api = api;
	}

	get hasOlder(): boolean {
		return this.#cursor !== null;
	}

	/** Старший msg_id: растёт — пришло новое сообщение (не правка и не страница истории). */
	get newest(): number | null {
		return this.messages.at(-1)?.msg_id ?? null;
	}

	readonly entries: ChatEntry[] = $derived.by(() => {
		const out: ChatEntry[] = [];
		const notes = [...this.notes];
		const flush = (upTo: number) => {
			while (notes.length > 0 && notes[0]!.after < upTo) {
				const n = notes.shift()!;
				out.push({ type: 'note', key: `n${n.id}`, note: n });
			}
		};
		for (const m of this.messages) {
			flush(m.msg_id);
			out.push({ type: 'message', key: `m${m.msg_id}`, message: m });
		}
		flush(Number.POSITIVE_INFINITY);
		return out;
	});

	/** Чат игры из настроек и последняя страница истории; повтор после ошибки — то же. */
	start(): Promise<void> {
		this.#pending = this.#start();
		return this.#pending;
	}

	/** Дождаться начатой загрузки (для тестов и перечитывания по reset). */
	idle(): Promise<void> {
		return this.#pending;
	}

	async #start(): Promise<void> {
		this.loading = true;
		this.error = null;
		try {
			if (this.chatId === null) this.chatId = gameChatId(await call(this.#api.GET('/settings')));
			await this.#reload();
		} catch (e) {
			if (e instanceof ApiFailure) this.error = e.error;
			else throw e;
		} finally {
			this.loading = false;
		}
	}

	async #page(cursor: string | null): Promise<{ items: MessageItem[]; next: string | null }> {
		const query = {
			types: 'message',
			chat_id: this.chatId,
			limit: CHAT_PAGE,
			...(cursor ? { cursor } : {})
		};
		const page = await call(this.#api.GET('/journal', { params: { query } }));
		const items = page.items.filter((i): i is MessageItem => i.type === 'message');
		return { items, next: page.next_cursor };
	}

	async #reload(): Promise<void> {
		if (this.chatId === null) return;
		const generation = ++this.#generation;
		const before = new Set(this.messages);
		const { items, next } = await this.#page(null);
		if (generation !== this.#generation) return;
		// Кадры, пришедшие во время запроса, свежее страницы: поверх неё.
		const arrived = this.messages.filter((m) => !before.has(m));
		this.messages = merge(merge([], items.map(fromItem)), arrived);
		this.#cursor = next;
		this.loaded = true;
	}

	async loadOlder(): Promise<void> {
		const cursor = this.#cursor;
		if (cursor === null || this.loadingOlder) return;
		const generation = this.#generation;
		this.loadingOlder = true;
		this.olderError = null;
		try {
			const { items, next } = await this.#page(cursor);
			if (generation !== this.#generation) return;
			this.messages = merge(this.messages, items.map(fromItem));
			this.#cursor = next;
		} catch (e) {
			if (e instanceof ApiFailure) this.olderError = e.error;
			else throw e;
		} finally {
			this.loadingOlder = false;
		}
	}

	onEvent(event: LiveEvent): void {
		if (event.type === 'message') {
			if (this.chatId === null || event.data.chat_id !== this.chatId) return;
			this.messages = upsert(this.messages, fromLive(event.data));
		} else if (event.type === 'reset' && this.loaded) {
			// Поток начат заново: пропущенное между кадрами — только в журнале.
			this.#pending = this.#reload().catch((e: unknown) => {
				if (e instanceof ApiFailure) this.error = e.error;
			});
		}
	}

	/** Системная строка в чат: итог команды, который не придёт сообщением игры. */
	note(text: string, kind: ChatNote['kind']): void {
		this.notes = [...this.notes, { id: ++this.#noteSeq, after: this.newest ?? 0, text, kind }];
	}
}
