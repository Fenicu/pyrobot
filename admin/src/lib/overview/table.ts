/** Сводка по аккаунтам: объединение с `GET /accounts`, сортировка, итоги, подписи. */
import type { AccountOut, OverviewAccount, OverviewBattle, OverviewNextAction, WakeKind } from '$lib/api/types';
import { noteText } from '$lib/overview/battle';
import { readyText, scenarioText, WAKE } from '$lib/plan/text';
import { fmtMoment } from '$lib/util/format';
import { SKILL_MARK } from '$lib/util/game';

export interface OverviewRow {
	account: AccountOut;
	/** null - сводки ещё нет (аккаунт только что создан или она не пришла). */
	extra: OverviewAccount | null;
}

export type SortKey =
	| 'name'
	| 'level'
	| 'expShare'
	| 'money'
	| 'stamina'
	| 'motivation'
	| 'busyUntil'
	| 'battleReady'
	| 'problems';

export type SortDir = 'asc' | 'desc';

export interface SortState {
	/** null - порядок пользователя. */
	key: SortKey | null;
	dir: SortDir;
}

const SORT_KEYS: readonly SortKey[] = [
	'name',
	'level',
	'expShare',
	'money',
	'stamina',
	'motivation',
	'busyUntil',
	'battleReady',
	'problems'
];

/** Клик по заголовку: по возрастанию, затем по убыванию, затем порядок пользователя. */
export function nextSort(current: SortState, key: SortKey): SortState {
	if (current.key !== key) return { key, dir: 'asc' };
	return current.dir === 'asc' ? { key, dir: 'desc' } : { key: null, dir: 'asc' };
}

export const SORT_KEY = 'pyrobot.overview.sort';

/** Сортировка помнится между визитами (это вид, не данные); хранилище недоступно или в нём мусор -
 * порядок пользователя. */
export function loadSort(): SortState {
	try {
		const raw: unknown = JSON.parse(localStorage.getItem(SORT_KEY) ?? 'null');
		if (typeof raw === 'object' && raw !== null) {
			const { key, dir } = raw as { key?: unknown; dir?: unknown };
			if (SORT_KEYS.includes(key as SortKey) && (dir === 'asc' || dir === 'desc')) {
				return { key: key as SortKey, dir };
			}
		}
	} catch {
		// пустое или битое значение
	}
	return { key: null, dir: 'asc' };
}

export function saveSort(state: SortState): void {
	try {
		if (state.key === null) localStorage.removeItem(SORT_KEY);
		else localStorage.setItem(SORT_KEY, JSON.stringify(state));
	} catch {
		// запрет хранилища: выбор живёт до перезагрузки
	}
}

/** Строки в порядке аккаунтов; сводка без аккаунта (удалён между опросами) отбрасывается. */
export function mergeRows(accounts: AccountOut[], extra: OverviewAccount[]): OverviewRow[] {
	const byId = new Map(extra.map((e) => [e.id, e]));
	return accounts.map((account) => ({ account, extra: byId.get(account.id) ?? null }));
}

/** В игре выставлена не та цель, что нужна. Неизвестное расхождением не считается. */
export function battleMismatch(row: OverviewRow): boolean {
	const battle = row.extra?.battle;
	return battle != null && battle.in_game !== null && battle.in_game !== battle.desired;
}

/** Не выставлена (0), неизвестно (1), выставлена (2); битвы нет - null. */
function readyRank(battle: OverviewBattle | null | undefined): number | null {
	if (battle == null) return null;
	return battle.ready === false ? 0 : battle.ready === null ? 1 : 2;
}

type Sortable = string | number | null;

function sortValue(row: OverviewRow, key: SortKey): Sortable {
	const { account, extra } = row;
	switch (key) {
		case 'name':
			return account.name;
		case 'level':
			return account.level;
		case 'expShare':
			return extra?.exp != null && extra.exp_next ? extra.exp / extra.exp_next : null;
		case 'money':
			return extra?.money ?? null;
		case 'stamina':
			return extra?.stamina ?? null;
		case 'motivation':
			return extra?.motivation ?? null;
		case 'busyUntil':
			return account.busy ? Date.parse(account.busy.until) : null;
		case 'battleReady':
			return readyRank(extra?.battle);
		case 'problems':
			return rowProblems(row).length;
	}
}

/** Новый список; пустые значения всегда в конце, равные держат прежний порядок. Ключ null -
 * порядок пользователя. */
export function sortRows(rows: OverviewRow[], key: SortKey | null, dir: SortDir): OverviewRow[] {
	if (key === null) return [...rows];
	const sign = dir === 'asc' ? 1 : -1;
	const keyed = rows.map((row, index) => ({ row, index, value: sortValue(row, key) }));
	keyed.sort((a, b) => {
		if (a.value === null || b.value === null) {
			return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1;
		}
		const diff =
			typeof a.value === 'string' && typeof b.value === 'string'
				? a.value.localeCompare(b.value, 'ru')
				: Number(a.value) - Number(b.value);
		return diff * sign || a.index - b.index;
	});
	return keyed.map((k) => k.row);
}

export interface Totals {
	money: number;
	knowledge: number;
	details: number;
	raw: number;
}

export function totalsRow(rows: OverviewRow[]): Totals {
	const total: Totals = { money: 0, knowledge: 0, details: 0, raw: 0 };
	for (const { extra } of rows) {
		if (extra === null) continue;
		total.money += extra.money ?? 0;
		total.knowledge += extra.knowledge ?? 0;
		total.details += extra.details ?? 0;
		total.raw += extra.raw ?? 0;
	}
	return total;
}

/** «Следующее действие бота»: подпись и, у ожидания, срок. */
export function nextActionText(action: OverviewNextAction): { text: string; at: string | null } {
	if (action.kind === 'act') {
		return { text: scenarioText(action.scenario ?? ''), at: null };
	}
	if (action.reason === 'no_timers') return { text: '⏳ ждёт событий', at: null };
	const kind = action.reason.split(':')[0]!;
	const text = kind in WAKE ? WAKE[kind as WakeKind].text : action.reason;
	return { text: `⏳ ${text}`, at: action.until };
}

/** Что не так со строкой, по важности; у выключенного и удаляемого аккаунта - только то, что
 * видно без движка. */
export function rowProblems(row: OverviewRow): string[] {
	const { account, extra } = row;
	const out: string[] = [];
	if (account.alert) out.push(account.alert.text);
	if (account.status === 'error') out.push(account.status_reason ?? 'ошибка');
	if (account.blocked) out.push(account.blocked_reason ? `заблокирован: ${account.blocked_reason}` : 'заблокирован');
	if (account.status !== 'enabled') return out;
	if (!account.tg.online) out.push('Telegram не в сети');
	if (extra === null) return out;
	if (extra.engine === 'stopped') out.push('движок не запущен');
	const blocked = extra.next_action?.blocked_by ?? null;
	if (blocked !== null) out.push(readyText(blocked));
	if (extra.stale) out.push('снимок устарел');
	const battle = extra.battle;
	if (battle != null) {
		if (battleMismatch(row)) out.push(`цель: в игре ${battle.in_game}, нужна ${battle.desired}`);
		const hidden = battle.note === blocked || battle.note?.startsWith('engine_');
		if (!battle.applies && battle.note !== null && !hidden) out.push(`цель: ${noteText(battle.note)}`);
	}
	return out;
}

/** Метро: идёт забег, когда снова доступно или «готово». */
export function metroText(row: OverviewRow, now: Date): string {
	if (row.account.in_metro) return 'идёт';
	const ready = row.extra?.metro?.ready_at;
	if (!ready) return '—';
	return Date.parse(ready) <= now.getTime() ? 'готово' : `в ${fmtMoment(ready, now)}`;
}

/** Сбор артефакта: до какого момента идёт. */
export function artifactText(extra: OverviewAccount | null, now: Date): string {
	const ends = extra?.artifact?.ends_at;
	if (!ends) return '—';
	return Date.parse(ends) <= now.getTime() ? 'завершён' : `до ${fmtMoment(ends, now)}`;
}

const SKILLS = ['practice', 'theory', 'cunning', 'wisdom'] as const;

/** «🔨31 🎓28 🐿12 🐢9»; навыков нет - прочерк. */
export function skillsText(extra: OverviewAccount | null): string {
	const skills = extra?.skills;
	return skills ? SKILLS.map((k) => `${SKILL_MARK[k]}${skills[k]}`).join(' ') : '—';
}
