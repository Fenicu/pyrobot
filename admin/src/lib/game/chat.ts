/** Чат игры в админке: сообщения журнала и кадры потока в одной форме. Каждая правка в журнале —
 * своя строка: на экране одно сообщение на msg_id со старшей ревизией. */
import type { InlineButton, Markup, MessageItem } from '$lib/api/types';
import type { SseMessage } from '$lib/live/sse';

export type Sender = 'planner' | 'manual' | null;

export interface ChatMessage {
	chat_id: number;
	msg_id: number;
	/** 0 — исходное, иначе момент правки в секундах. */
	revision: number;
	edited: boolean;
	/** Момент отправки, у правки — момент правки. */
	date: string;
	outgoing: boolean;
	text: string | null;
	markup: Markup | null;
	/** Своё сообщение: бот или ручная команда из админки; null — входящее или набрано не здесь. */
	source: Sender;
}

export function fromItem(item: MessageItem): ChatMessage {
	return {
		chat_id: item.chat_id,
		msg_id: item.msg_id,
		revision: item.revision,
		edited: item.kind === 'edit',
		date: item.date,
		outgoing: item.outgoing,
		text: item.text,
		markup: (item.markup ?? null) as Markup | null,
		source: item.source ?? null
	};
}

export function fromLive(d: SseMessage): ChatMessage {
	return {
		chat_id: d.chat_id,
		msg_id: d.msg_id,
		revision: d.revision,
		edited: d.kind === 'edit',
		date: d.date,
		outgoing: d.outgoing,
		text: d.text,
		markup: d.markup ?? null,
		source: d.source ?? null
	};
}

/** Сообщение в список по msg_id (он в чате Telegram растёт): старшая или та же ревизия заменяет,
 * младшая — нет. Список — по возрастанию msg_id. */
export function upsert(list: readonly ChatMessage[], msg: ChatMessage): ChatMessage[] {
	const at = list.findIndex((m) => m.msg_id === msg.msg_id);
	if (at !== -1) {
		const prev = list[at]!;
		if (msg.revision < prev.revision) return list as ChatMessage[];
		const next = [...list];
		next[at] = { ...msg, source: msg.source ?? prev.source };
		return next;
	}
	let i = list.length;
	while (i > 0 && list[i - 1]!.msg_id > msg.msg_id) i--;
	return [...list.slice(0, i), msg, ...list.slice(i)];
}

export function merge(list: readonly ChatMessage[], msgs: readonly ChatMessage[]): ChatMessage[] {
	return msgs.reduce<ChatMessage[]>((acc, m) => upsert(acc, m), [...list]);
}

/** Кнопки сообщения по рядам, в ряду — по колонкам. */
export function inlineRows(markup: Markup | null | undefined): InlineButton[][] {
	const out: InlineButton[][] = [];
	for (const b of markup?.inline ?? []) (out[b[1]] ??= []).push(b);
	return out.filter(Boolean).map((r) => r.sort((a, b) => a[2] - b[2]));
}

/** Клавиатура под полем ввода — у последнего входящего сообщения, где она есть. */
export function replyKeyboard(list: readonly ChatMessage[]): string[][] | null {
	for (let i = list.length - 1; i >= 0; i--) {
		const m = list[i]!;
		if (!m.outgoing && m.markup?.reply?.length) return m.markup.reply;
	}
	return null;
}

/** Ссылка кнопки открывается только по http(s). */
export function safeUrl(url: string | null | undefined): string | null {
	return url && /^https?:\/\//i.test(url) ? url : null;
}
