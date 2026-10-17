/** Массовые действия «Сводки»: путь настройки в `changes`, листья для выбора, итоги операции. */
import { call, type Api } from '$lib/api/client';
import type { EngineBulkOut, EngineBulkResult, SettingsBulkIn, SettingsBulkOut, SettingsBulkResult } from '$lib/api/types';
import type { OverviewRow } from '$lib/overview/table';
import { settingLabel } from '$lib/settings/labels';
import {
	editable,
	leaves,
	pathKey,
	resolve,
	sectionsOf,
	type Field,
	type FieldKind,
	type JsonSchema
} from '$lib/settings/schema';

/** За сколько до битвы пауза мешает выставить цель. */
export const BATTLE_WARN_MS = 2 * 3_600_000;

/** `('battle.target', X)` → `{battle: {target: X}}`. */
export function pathToChanges(path: string, value: unknown): Record<string, unknown> {
	return path
		.split('.')
		.reverse()
		.reduce<unknown>((inner, key) => ({ [key]: inner }), value) as Record<string, unknown>;
}

export interface LeafOption {
	/** `battle.target` */
	path: string;
	label: string;
	/** Подпись раздела («Битва»). */
	section: string;
	field: Field;
}

/** Редактируемые листья схемы настроек: те же, что в «Настройках», без «только чтение». */
export function editableLeaves(schema: JsonSchema): LeafOption[] {
	return sectionsOf(schema).flatMap((section) =>
		leaves(editable(section.fields)).map((field) => {
			const path = pathKey(field.path);
			return {
				path,
				label: settingLabel(path, field.title),
				section: settingLabel(section.name, section.title),
				field
			};
		})
	);
}

/** Поиск по подписи, пути и разделу; пустой запрос - всё. */
export function searchLeaves(options: LeafOption[], query: string): LeafOption[] {
	const q = query.trim().toLowerCase();
	if (!q) return options;
	return options.filter((o) => `${o.label} ${o.path} ${o.section}`.toLowerCase().includes(q));
}

function blankOf(kind: FieldKind): unknown {
	switch (kind.kind) {
		case 'boolean':
			return false;
		case 'enum':
			return kind.options[0] ?? '';
		case 'number':
			return kind.min ?? 0;
		case 'max_or_int':
			return kind.min ?? 0;
		case 'enum_tags':
		case 'string_tags':
			return [];
		case 'nullable':
			return null;
		case 'map':
			return {};
		case 'json':
			return null;
		default:
			return '';
	}
}

/** Значение, с которого начинается поле: умолчание схемы, иначе пустое по виду поля. */
export function initialValue(schema: JsonSchema, field: Field): unknown {
	let node: JsonSchema | undefined = schema;
	for (const key of field.path) node = node && resolve(node, schema).properties?.[key];
	const fallback = node ? resolve(node, schema).default : undefined;
	return fallback !== undefined ? fallback : blankOf(field.type);
}

/** Выбор строк «Сводки» для массовых действий. */
export interface Selection {
	selected: ReadonlySet<number>;
	toggle: (id: number) => void;
	toggleAll: (on: boolean) => void;
}

export type BulkGroup = 'applied' | 'unchanged' | 'skipped' | 'error';

export interface ResultRow {
	accountId: number;
	group: BulkGroup;
	text: string;
}

export interface BulkSummary {
	applied: number;
	unchanged: number;
	skipped: number;
	error: number;
	rows: ResultRow[];
}

const HOST: Record<string, string> = {
	locked_elsewhere: 'работает на другом хосте',
	lease_active: 'аренда аккаунта занята'
};

const REASON: Record<string, string> = {
	already_paused: 'уже на паузе',
	already_running: 'уже работает',
	account_not_found: 'аккаунт не найден',
	account_deleting: 'аккаунт удаляется',
	version_conflict: 'настройки менялись во время записи, повторите',
	engine_starting: 'движок запускается, повторите позже',
	lock_lost: 'потеряна аренда аккаунта',
	live_requires_confirm: 'нужно подтверждение LIVE',
	internal_error: 'внутренняя ошибка, подробности в логе сервера'
};

function reasonText(code: string, extra: { host?: string | null; path?: string | null; loc?: (string | number)[] | null }): string {
	switch (code) {
		case 'engine_not_running':
			return extra.host ? `движок не запущен: ${HOST[extra.host] ?? extra.host}` : 'движок не запущен';
		case 'setting_out_of_bounds':
			return `значение за границами сервера${extra.path ? `: ${extra.path}` : ''}`;
		case 'chat_is_self':
			return `чат равен самому аккаунту${extra.path ? `: ${extra.path}` : ''}`;
		case 'validation_error':
			return `значение не подходит${extra.loc?.length ? `: ${extra.loc.join('.')}` : ''}`;
		default:
			return REASON[code] ?? code;
	}
}

function engineRow(r: EngineBulkResult): ResultRow {
	const group: BulkGroup = r.status === 'done' ? 'applied' : r.status;
	if (r.status !== 'done') return { accountId: r.account_id, group, text: reasonText(r.reason ?? '', { host: r.host }) };
	const note = (r.warnings ?? []).includes('audit_failed') ? '; записано, но запись в журнал уведомлений не удалась' : '';
	return { accountId: r.account_id, group, text: `готово${note}` };
}

function settingsRow(r: SettingsBulkResult): ResultRow {
	if (r.status === 'error') {
		const e = r.error;
		return { accountId: r.account_id, group: 'error', text: reasonText(e?.code ?? '', { path: e?.path, loc: e?.loc }) };
	}
	if (r.status === 'unchanged') return { accountId: r.account_id, group: 'unchanged', text: 'значение уже такое' };
	const notes: string[] = [];
	if (r.restart_required.length > 0) notes.push('применится после перезапуска аккаунта');
	if ((r.warnings ?? []).includes('audit_failed')) notes.push('записано, но запись в журнал уведомлений не удалась');
	return { accountId: r.account_id, group: 'applied', text: notes.length ? `готово; ${notes.join('; ')}` : 'готово' };
}

/** Итоги операции для диалога: счётчики групп и строка с причиной на каждый аккаунт. */
export function summarizeResults(results: (EngineBulkResult | SettingsBulkResult)[]): BulkSummary {
	const rows = results.map((r) => ('error' in r ? settingsRow(r) : engineRow(r)));
	const count = (g: BulkGroup) => rows.filter((r) => r.group === g).length;
	return { applied: count('applied'), unchanged: count('unchanged'), skipped: count('skipped'), error: count('error'), rows };
}

/** Ближайшая битва среди аккаунтов и надо ли на неё ещё выставлять цель (в игре она не подтверждена). */
export function battleOutlook(rows: OverviewRow[]): { at: string | null; needsTarget: boolean } {
	const battles = rows.flatMap((r) => (r.extra?.battle ? [r.extra.battle] : []));
	if (battles.length === 0) return { at: null, needsTarget: false };
	const at = battles.reduce((min, b) => (Date.parse(b.battle_at) < Date.parse(min) ? b.battle_at : min), battles[0]!.battle_at);
	return { at, needsTarget: battles.some((b) => b.battle_at === at && b.ready !== true) };
}

/** Пауза за два часа и меньше до битвы, на которую цель ещё не выставлена: планировщик на паузе
 * решений не принимает. */
export function pauseWarning(now: Date, nextBattleAt: string | null, needsTarget: boolean): string | null {
	if (nextBattleAt === null || !needsTarget) return null;
	const left = Date.parse(nextBattleAt) - now.getTime();
	if (left < 0 || left > BATTLE_WARN_MS) return null;
	return 'Битва меньше чем через два часа: на паузе бот не принимает решений, цель на неё не будет выставлена.';
}

export const pauseAll = (api: Api, action: 'pause' | 'resume', ids: number[] | null): Promise<EngineBulkOut> =>
	call(api.POST('/api/v1/overview/engine', { body: { action, account_ids: ids } }));

export const changeSettings = (api: Api, body: SettingsBulkIn): Promise<SettingsBulkOut> =>
	call(api.POST('/api/v1/overview/settings', { body }));
