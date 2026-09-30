import type { Markup, PublicState } from '$lib/api/types';

/** Кадры потока `/api/v1/accounts/{id}/events` (`app/engine/stream.py`). */
export interface SseMessage {
	journal_id: number;
	chat_id: number;
	msg_id: number;
	revision: number;
	kind: string;
	date: string;
	outgoing: boolean;
	text: string | null;
	events: unknown[];
	/** Кнопки как у элемента /journal; у сервера до 0.4 поля нет — null. */
	markup: Markup | null;
}
export interface SseState {
	version: number;
	/** Изменившиеся корневые поля: значение заменяет поле целиком, null — поле сброшено. */
	changed: Partial<Record<keyof PublicState, unknown>>;
}
export interface SseActionCreated {
	id: number;
	status: string;
	reason: string;
	source: string;
	kind: string;
	chat_id: number;
	text: string | null;
	data: string | null;
	/** Сообщение действия и название чата пересылки; у сервера до 0.7 полей нет. */
	message_id?: number | null;
	chat_title?: string | null;
	command_class: string;
	/** Запуск сценария, шагом которого идёт действие; у сервера до 0.4 поля нет — null. */
	scenario_run_id: number | null;
}
export interface SseActionUpdate {
	id: number;
	status: string;
	reason: string;
}
export interface SseDecision {
	id: number;
	at: string;
	kind: string;
	scenario: string | null;
	reason: string;
	until: string | null;
}
export interface SseScenarioRun {
	id: number;
	scenario: string | null;
	status: string;
	reason: string;
	/** Решение, с которого начат запуск: только в кадре начала; у сервера до 0.9 поля нет. */
	decision_id?: number | null;
}
export interface SseNotification {
	id: number;
	level: string;
	code: string;
	text: string;
}
export interface SseSettings {
	version: number;
	mode: string;
	paused: boolean;
	killed: boolean;
}
export type ResetReason = 'new' | 'unknown' | 'evicted' | 'epoch';

export type LiveEvent =
	| { type: 'message'; id: string; data: SseMessage }
	| { type: 'state'; id: string; data: SseState }
	| { type: 'action'; id: string; data: SseActionCreated | SseActionUpdate }
	| { type: 'decision'; id: string; data: SseDecision }
	| { type: 'scenario_run'; id: string; data: SseScenarioRun }
	| { type: 'notification'; id: string; data: SseNotification }
	| { type: 'settings'; id: string; data: SseSettings }
	| { type: 'reset'; id: string; data: { reason: ResetReason } };

export type LiveType = LiveEvent['type'];
export const LIVE_TYPES: readonly LiveType[] = [
	'message',
	'state',
	'action',
	'decision',
	'scenario_run',
	'notification',
	'settings',
	'reset'
];
const RESETS = new Set(['new', 'unknown', 'evicted', 'epoch']);

type Rec = Record<string, unknown>;
const num = (v: unknown) => typeof v === 'number';
const str = (v: unknown) => typeof v === 'string';
const strOrNull = (v: unknown) => v === null || typeof v === 'string';

function valid(type: LiveType, d: Rec): boolean {
	switch (type) {
		case 'message':
			return num(d.journal_id) && num(d.chat_id) && num(d.msg_id) && num(d.revision) && strOrNull(d.text);
		case 'state':
			return num(d.version) && typeof d.changed === 'object' && d.changed !== null;
		case 'action':
			return num(d.id) && str(d.status);
		case 'decision':
			return num(d.id) && str(d.kind) && str(d.at);
		case 'scenario_run':
			return num(d.id) && str(d.status);
		case 'notification':
			return num(d.id) && str(d.level) && str(d.code) && str(d.text);
		case 'settings':
			return num(d.version) && str(d.mode);
		case 'reset':
			return RESETS.has(String(d.reason));
	}
}

/** Кадр потока в типизированное событие; незнакомый тип или битые данные — null. */
export function decodeEvent(type: string, raw: string, id = ''): LiveEvent | null {
	if (!(LIVE_TYPES as readonly string[]).includes(type)) return null;
	let data: unknown;
	try {
		data = JSON.parse(raw);
	} catch {
		return null;
	}
	if (typeof data !== 'object' || data === null || Array.isArray(data)) return null;
	const rec = data as Rec;
	if (!valid(type as LiveType, rec)) return null;
	if (type === 'message' && rec.markup === undefined) rec.markup = null;
	if (type === 'action' && rec.reason === undefined) rec.reason = '';
	if (type === 'action' && 'source' in rec && rec.scenario_run_id === undefined) rec.scenario_run_id = null;
	return { type, id, data: rec } as LiveEvent;
}

export function isActionCreated(d: SseActionCreated | SseActionUpdate): d is SseActionCreated {
	return 'source' in d;
}

export interface RawFrame {
	id: string;
	event: string;
	data: string;
}

/** Разбор текста SSE на кадры (как это делает EventSource): для фикстур и отладки. */
export function splitFrames(text: string): RawFrame[] {
	const frames: RawFrame[] = [];
	for (const block of text.replace(/\r\n/g, '\n').split('\n\n')) {
		let id = '';
		let event = 'message';
		const data: string[] = [];
		let any = false;
		for (const line of block.split('\n')) {
			if (line === '' || line.startsWith(':')) continue;
			const colon = line.indexOf(':');
			const field = colon === -1 ? line : line.slice(0, colon);
			let value = colon === -1 ? '' : line.slice(colon + 1);
			if (value.startsWith(' ')) value = value.slice(1);
			any = true;
			if (field === 'id') id = value;
			else if (field === 'event') event = value;
			else if (field === 'data') data.push(value);
		}
		if (any && data.length > 0) frames.push({ id, event, data: data.join('\n') });
	}
	return frames;
}
