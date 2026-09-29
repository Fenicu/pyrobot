import type { ActionItem, DecisionItem, JournalItem, MessageItem } from '$lib/api/types';
import { keyOf } from '$lib/stores/journal.svelte';
import { ACTION_STATUS, statusTone } from '$lib/util/game';
import { firstLine } from '$lib/util/text';

/** Запуск сценария одной строкой: решение, с которого он начат, его шаги и ответы игры на них. */
export interface RunGroup {
	kind: 'run';
	key: string;
	runId: number;
	decision: DecisionItem | null;
	actions: ActionItem[];
	messages: MessageItem[];
	/** Все записи группы в порядке ленты — от новых к старым. */
	items: JournalItem[];
	/** Начало запуска — самая старая запись группы. */
	started: string;
}

export type ChronicleRow = { kind: 'item'; key: string; item: JournalItem } | RunGroup;

/** Ответ игры досылается чуть позже итога шага: правка текста, второе сообщение. */
export const REPLY_SLACK_MS = 3_000;
/** Шаг без итога в строке ленты (живой, ещё идёт): ответы ждём не дольше этого. */
export const OPEN_STEP_MS = 30_000;

const ms = (at: string) => new Date(at).getTime();

/** Лента → хроника. Шаги одного запуска (`run_id`) и решение, которое его начало, — одна строка;
 * к ней — сообщения игры из тех же чатов, пришедшие от начала запуска до итога последнего шага
 * (с запасом REPLY_SLACK_MS). Остальное — отдельными строками, как было. Место строки запуска —
 * по самой новой его записи; решение без шагов на загруженных страницах — обычная строка. */
export function chronicle(items: JournalItem[]): ChronicleRow[] {
	const runs = new Map<number, RunGroup>();
	const group = (runId: number) => {
		let g = runs.get(runId);
		if (!g) {
			g = { kind: 'run', key: `run:${runId}`, runId, decision: null, actions: [], messages: [], items: [], started: '' };
			runs.set(runId, g);
		}
		return g;
	};
	for (const item of items) {
		if (item.type === 'action' && item.run_id != null) group(item.run_id).actions.push(item);
		else if (item.type === 'decision' && item.run_id != null) group(item.run_id).decision = item;
	}
	const groups = [...runs.values()].filter((g) => g.actions.length > 0);
	const windows = groups.map((g) => {
		const starts = [...g.actions, ...(g.decision ? [g.decision] : [])].map((i) => ms(i.at));
		const ends = g.actions.map((a) => (a.finished_at ? ms(a.finished_at) : ms(a.at) + OPEN_STEP_MS));
		return {
			g,
			chats: new Set(g.actions.map((a) => a.chat_id)),
			from: Math.min(...starts),
			to: Math.max(...ends) + REPLY_SLACK_MS
		};
	});
	const owner = new Map<string, RunGroup>();
	for (const g of groups) {
		for (const a of g.actions) owner.set(keyOf(a), g);
		if (g.decision) owner.set(keyOf(g.decision), g);
	}
	for (const item of items) {
		if (item.type !== 'message') continue;
		const at = ms(item.at);
		const hit = windows.find((w) => w.chats.has(item.chat_id) && at >= w.from && at <= w.to);
		if (hit) {
			hit.g.messages.push(item);
			owner.set(keyOf(item), hit.g);
		}
	}
	const rows: ChronicleRow[] = [];
	const emitted = new Set<RunGroup>();
	for (const item of items) {
		const g = owner.get(keyOf(item));
		if (!g) {
			rows.push({ kind: 'item', key: keyOf(item), item });
			continue;
		}
		g.items.push(item);
		g.started = item.at;
		if (!emitted.has(g)) {
			emitted.add(g);
			rows.push(g);
		}
	}
	return rows;
}

/** Итог запуска по его шагам: неудача — статусом последнего неудачного шага, идущий — «идёт». */
export function runOutcome(g: RunGroup): { text: string; tone: 'ok' | 'warn' | 'bad' | 'muted' } {
	const failed = g.actions.find((a) => statusTone(a.status) === 'bad');
	if (failed) return { text: ACTION_STATUS[failed.status] ?? failed.status, tone: 'bad' };
	if (g.actions.some((a) => ['intent', 'sent'].includes(a.status))) return { text: 'идёт', tone: 'muted' };
	const suppressed = g.actions.find((a) => a.status === 'suppressed');
	if (suppressed) return { text: ACTION_STATUS.suppressed!, tone: 'warn' };
	return { text: 'выполнено', tone: 'ok' };
}

/** Чем кончился запуск словами игры: первая строка самого нового её ответа. */
export function runReply(g: RunGroup): string {
	const reply = g.messages.find((m) => !m.outgoing && firstLine(m.text));
	return reply ? firstLine(reply.text) : '';
}

function plural(n: number, [one, few, many]: [string, string, string]): string {
	const d = n % 10;
	const dd = n % 100;
	const word = d === 1 && dd !== 11 ? one : d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many;
	return `${n} ${word}`;
}

/** «3 команды · 4 сообщения». */
export function runCounts(g: RunGroup): string {
	const parts = [plural(g.actions.length, ['команда', 'команды', 'команд'])];
	if (g.messages.length) parts.push(plural(g.messages.length, ['сообщение', 'сообщения', 'сообщений']));
	return parts.join(' · ');
}
