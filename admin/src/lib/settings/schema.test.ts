import { describe, expect, it } from 'vitest';
import type { SettingsOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import {
	buildChanges,
	changedPaths,
	editable,
	leaves,
	sectionsOf,
	setAt,
	type Field,
	type JsonSchema
} from './schema';
import generated from './settings.schema.json';

const settings = fixture<SettingsOut>('settings');
const root = settings.schema as JsonSchema;
const sections = sectionsOf(root);
const field = (path: string): Field => {
	const f = sections.flatMap((s) => leaves(s.fields)).find((x) => x.path.join('.') === path);
	if (!f) throw new Error(path);
	return f;
};

describe('форма по схеме настроек с прода', () => {
	it('секции — корневые свойства', () => {
		expect(sections.map((s) => s.name)).toEqual([
			'engine', 'chats', 'features', 'strategy', 'food', 'sleep', 'levelup', 'battle',
			'stocks', 'tangerine', 'metro', 'daily', 'lottery'
		]);
		expect(sections.find((s) => s.name === 'features')?.description).toMatch(/Включённые механики/);
	});

	it('форма — без полей «только чтение»: kill, его причина и пауза меняются на главной', () => {
		const shown = sections.flatMap((s) => leaves(editable(s.fields))).map((f) => f.path.join('.'));
		expect(shown).not.toContain('engine.killed');
		expect(shown).not.toContain('engine.kill_reason');
		expect(shown).not.toContain('engine.paused');
		expect(shown).toContain('engine.mode');
		// Остальное — на месте, группы тоже.
		expect(shown).toHaveLength(sections.flatMap((s) => leaves(s.fields)).length - 3);
		expect(shown).toContain('lottery.keep.money');
	});

	it('типы полей', () => {
		expect(field('engine.mode').type).toEqual({ kind: 'enum', options: ['dry_run', 'live'] });
		expect(field('engine.killed')).toMatchObject({ readOnly: true, type: { kind: 'boolean' } });
		expect(field('engine.kill_reason')).toMatchObject({
			readOnly: true,
			type: { kind: 'nullable', inner: { kind: 'string' } }
		});
		expect(field('engine.click_answer_timeout_s').type).toEqual({
			kind: 'number', integer: false, min: 0, max: 30, exclusiveMin: true, exclusiveMax: false
		});
		expect(field('features.lottery').type).toEqual({ kind: 'boolean' });
		expect(field('strategy.focus').type).toEqual({
			kind: 'enum_tags',
			options: ['harvest', 'job', 'learn', 'dconv', 'walk', 'confa', 'rob']
		});
		expect(field('sleep.hotel_if_cash_after_reserve_ge').type).toMatchObject({
			kind: 'nullable',
			inner: { kind: 'number', integer: true }
		});
		expect(field('levelup.policy').type).toEqual({ kind: 'const', value: 'balanced' });
		expect(field('battle.overrides').type).toMatchObject({ kind: 'map', value: { kind: 'enum' } });
		expect(field('lottery.tickets.money').type).toEqual({ kind: 'max_or_int', min: 0 });
		expect(field('lottery.keep.money').type).toMatchObject({ kind: 'number', integer: true, min: 0 });
		const lottery = sections.find((s) => s.name === 'lottery')!;
		expect(lottery.fields.map((f) => [f.name, f.type.kind])).toEqual([
			['tickets', 'group'],
			['keep', 'group']
		]);
	});

	it('неиспользуемые ботом настройки — по пометке x-unused в схеме бэкенда', () => {
		const current = sectionsOf(generated as JsonSchema).flatMap((s) => leaves(s.fields));
		// Файл схемы — с сортировкой ключей (tools/settings_schema.py).
		expect(current.filter((f) => f.unused).map((f) => f.path.join('.'))).toEqual([
			'features.arena',
			'features.casino',
			'gadgets.keep_money',
			'levelup.policy'
		]);
		// В схеме с прода пометки ещё нет — ни одного поля.
		expect(sections.flatMap((s) => leaves(s.fields)).some((f) => f.unused)).toBe(false);
	});

	it('diff по листьям и тело PATCH только изменённого', () => {
		let draft = setAt(settings.values, ['strategy', 'focus'], ['dconv']);
		draft = setAt(draft, ['lottery', 'tickets', 'money'], 5);
		draft = setAt(draft, ['engine', 'killed'], true);
		const paths = changedPaths(sections, settings.values, draft);
		expect(paths).toEqual([
			['strategy', 'focus'],
			['lottery', 'tickets', 'money']
		]);
		expect(buildChanges(draft, paths)).toEqual({
			strategy: { focus: ['dconv'] },
			lottery: { tickets: { money: 5 } }
		});
	});
});
