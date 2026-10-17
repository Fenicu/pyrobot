import { describe, expect, it } from 'vitest';
import { accountOut } from '$lib/test/overview';
import type { EngineBulkResult, OverviewBattle, SettingsBulkResult } from '$lib/api/types';
import schemaJson from '$lib/settings/settings.schema.json';
import type { JsonSchema } from '$lib/settings/schema';
import {
	BATTLE_WARN_MS,
	battleOutlook,
	editableLeaves,
	initialValue,
	pathToChanges,
	pauseWarning,
	searchLeaves,
	summarizeResults
} from './bulk';

const schema = schemaJson as unknown as JsonSchema;

const engine = (over: Partial<EngineBulkResult>): EngineBulkResult => ({
	account_id: 1,
	status: 'done',
	reason: null,
	host: null,
	...over
});
const settings = (over: Partial<SettingsBulkResult>): SettingsBulkResult => ({
	account_id: 1,
	status: 'applied',
	version: 2,
	changed: {},
	restart_required: [],
	error: null,
	warnings: [],
	...over
});

describe('pathToChanges', () => {
	it('вкладывает путь по секциям', () => {
		expect(pathToChanges('battle.target', 'X')).toEqual({ battle: { target: 'X' } });
		expect(pathToChanges('food.banana_reserve', 40)).toEqual({ food: { banana_reserve: 40 } });
	});
	it('значение-список и словарь кладёт целиком', () => {
		expect(pathToChanges('battle.overrides', { '22': 'A' })).toEqual({ battle: { overrides: { '22': 'A' } } });
	});
});

describe('editableLeaves', () => {
	const leaves = editableLeaves(schema);
	const paths = leaves.map((l) => l.path);
	it('только редактируемые листья схемы, у каждого подпись и раздел', () => {
		expect(paths).toContain('battle.target');
		expect(paths).toContain('food.banana_reserve');
		expect(paths).not.toContain('engine.killed');
		expect(paths).not.toContain('engine.paused');
		expect(paths.some((p) => p.startsWith('artifact_run'))).toBe(false);
		const target = leaves.find((l) => l.path === 'battle.target');
		expect(target?.label).toBeTruthy();
		expect(target?.section).toBeTruthy();
		expect(target?.field.type.kind).toBe('enum');
	});
	it('поиск по подписи, пути и разделу без учёта регистра', () => {
		const byPath = searchLeaves(leaves, 'BANANA_RESERVE').map((l) => l.path);
		expect(byPath).toEqual(['food.banana_reserve']);
		const target = leaves.find((l) => l.path === 'battle.target')!;
		expect(searchLeaves(leaves, target.label.slice(0, 5).toUpperCase()).map((l) => l.path)).toContain('battle.target');
		expect(searchLeaves(leaves, '  ')).toHaveLength(leaves.length);
		expect(searchLeaves(leaves, 'такого-нет-нигде')).toEqual([]);
	});
});

describe('summarizeResults', () => {
	it('считает по группам и даёт строки с причиной', () => {
		const out = summarizeResults([
			engine({ account_id: 1 }),
			engine({ account_id: 2, status: 'unchanged', reason: 'already_paused' }),
			engine({ account_id: 3, status: 'skipped', reason: 'engine_not_running', host: 'locked_elsewhere' }),
			engine({ account_id: 4, status: 'error', reason: 'lock_lost' })
		]);
		expect([out.applied, out.unchanged, out.skipped, out.error]).toEqual([1, 1, 1, 1]);
		expect(out.rows.map((r) => [r.accountId, r.group])).toEqual([
			[1, 'applied'],
			[2, 'unchanged'],
			[3, 'skipped'],
			[4, 'error']
		]);
		expect(out.rows[1]!.text).toBe('уже на паузе');
		expect(out.rows[2]!.text).toContain('другом хосте');
		expect(out.rows[3]!.text).toContain('аренда');
	});
	it('текст для каждого кода ошибки и причины', () => {
		const codes = [
			'account_not_found',
			'account_deleting',
			'version_conflict',
			'engine_starting',
			'lock_lost',
			'live_requires_confirm',
			'setting_out_of_bounds',
			'chat_is_self',
			'validation_error',
			'internal_error'
		];
		const out = summarizeResults(
			codes.map((code, i) =>
				settings({ account_id: i, status: 'error', version: null, error: { code, path: 'a.b', loc: ['a', 'b'] } })
			)
		);
		for (const row of out.rows) {
			expect(row.group).toBe('error');
			expect(row.text).not.toMatch(/^[a-z_]+$/);
		}
		expect(out.rows[6]!.text).toContain('a.b');
		expect(summarizeResults([engine({ status: 'unchanged', reason: 'already_running' })]).rows[0]!.text).toBe('уже работает');
		expect(summarizeResults([engine({ status: 'skipped', reason: 'engine_not_running', host: null })]).rows[0]!.text).toBe(
			'движок не запущен'
		);
	});
	it('настройки: применено с перезапуском и предупреждением аудита', () => {
		const out = summarizeResults([
			settings({ account_id: 1 }),
			settings({ account_id: 2, restart_required: ['chats.team_chat_id'] }),
			settings({ account_id: 3, warnings: ['audit_failed'] }),
			settings({ account_id: 4, status: 'unchanged' })
		]);
		expect([out.applied, out.unchanged]).toEqual([3, 1]);
		expect(out.rows[1]!.text).toContain('перезапуск');
		expect(out.rows[2]!.text).toContain('журнал');
		expect(out.rows[3]!.text).toBe('значение уже такое');
	});
});

describe('pauseWarning', () => {
	const now = new Date('2026-10-17T10:00:00Z');
	const at = (ms: number) => new Date(now.getTime() + ms).toISOString();
	it('меньше двух часов до битвы и цель ещё не выставлена - предупреждение', () => {
		expect(pauseWarning(now, at(30 * 60_000), true)).toMatch(/не будет выставлена/);
	});
	it('ровно два часа - ещё предупреждение, две минуты сверху - нет', () => {
		expect(pauseWarning(now, at(BATTLE_WARN_MS), true)).not.toBeNull();
		expect(pauseWarning(now, at(BATTLE_WARN_MS + 60_000), true)).toBeNull();
	});
	it('нет битвы, битва прошла или цели ставить не надо - тишина', () => {
		expect(pauseWarning(now, null, true)).toBeNull();
		expect(pauseWarning(now, at(-60_000), true)).toBeNull();
		expect(pauseWarning(now, at(60_000), false)).toBeNull();
	});
});

describe('initialValue', () => {
	const leaves = editableLeaves(schema);
	const by = (path: string) => leaves.find((l) => l.path === path)!;
	it('умолчание схемы, если оно есть', () => {
		expect(initialValue(schema, by('food.banana_reserve').field)).toBe(50);
		expect(initialValue(schema, by('battle.target').field)).toBe('🛡Защита');
	});
	it('иначе пустое по виду поля', () => {
		const blank = (kind: object) => initialValue(schema, { name: 'x', path: ['nope', 'x'], title: 'x', readOnly: false, unused: false, type: kind } as never);
		expect(blank({ kind: 'boolean' })).toBe(false);
		expect(blank({ kind: 'enum', options: ['a', 'b'] })).toBe('a');
		expect(blank({ kind: 'number', integer: true, min: 3 })).toBe(3);
		expect(blank({ kind: 'string_tags' })).toEqual([]);
		expect(blank({ kind: 'nullable', inner: { kind: 'string' } })).toBeNull();
	});
});

describe('battleOutlook', () => {
	const battle = (at: string, ready: boolean | null): OverviewBattle => ({
		battle_at: at,
		desired: '🛡Защита',
		source: 'default',
		in_game: null,
		ready,
		applies: true,
		note: null
	});
	const row = (id: number, b: OverviewBattle | null) => ({
		account: accountOut(id, `A${id}`),
		extra: { battle: b } as never
	});
	it('ближайшая битва и нужна ли цель', () => {
		const rows = [row(1, battle('2026-10-17T19:00:00Z', true)), row(2, battle('2026-10-17T10:00:00Z', false)), row(3, null)];
		expect(battleOutlook(rows)).toEqual({ at: '2026-10-17T10:00:00Z', needsTarget: true });
	});
	it('цель везде выставлена или битв нет', () => {
		expect(battleOutlook([row(1, battle('2026-10-17T10:00:00Z', true))])).toEqual({
			at: '2026-10-17T10:00:00Z',
			needsTarget: false
		});
		expect(battleOutlook([row(1, null)])).toEqual({ at: null, needsTarget: false });
		expect(battleOutlook([])).toEqual({ at: null, needsTarget: false });
	});
	it('неизвестное (ready=null) считается невыставленным', () => {
		expect(battleOutlook([row(1, battle('2026-10-17T10:00:00Z', null))]).needsTarget).toBe(true);
	});
});
