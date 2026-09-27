import { describe, expect, it } from 'vitest';
import type { ScenarioInfo } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { paramError, paramsOf } from './params';

const catalog = fixture<ScenarioInfo[]>('scenarios');
const spec = (name: string, param: string) => catalog.find((s) => s.name === name)!.required[param]!;

describe('ParamSpec из каталога с прода', () => {
	it('int с границами', () => {
		const hours = spec('sleep', 'hours');
		expect(paramError(hours, '')).toBe('обязательное поле');
		expect(paramError(hours, '6')).toBe('не меньше 7');
		expect(paramError(hours, '13')).toBe('не больше 12');
		expect(paramError(hours, '7.5')).toBe('нужно целое число');
		expect(paramError(hours, '12')).toBeNull();
		expect(paramError(spec('stocks_dump', 'keep'), '0')).toBeNull();
		expect(paramError(spec('tangerine', 'chat'), '-1001377961602')).toBeNull();
	});

	it('enum', () => {
		const target = spec('battle_target', 'target');
		expect(paramError(target, '📯Pied Piper')).toBeNull();
		expect(paramError(target, 'Nokia')).toBe('нет в списке');
	});

	it('string с шаблоном ECMA (в том числе эмодзи)', () => {
		const code = spec('bulls_join', 'code');
		expect(paramError(code, 'join_fight_AbC-12_xYz9')).toBeNull();
		expect(paramError(code, 'join_fight_short')).toMatch(/^не по шаблону/);
		const recipe = spec('smoothie', 'recipe');
		expect(paramError(recipe, '🍋🍇🍏🥕🍅')).toBeNull();
		expect(paramError(recipe, '🍋🍇🍏🥕')).toMatch(/^не по шаблону/);
	});

	it('значения формы — в параметры запуска', () => {
		const sleep = catalog.find((s) => s.name === 'sleep')!;
		expect(paramsOf(sleep.required, { hours: ' 8 ' })).toEqual({ hours: 8 });
		const tangerine = catalog.find((s) => s.name === 'tangerine')!;
		expect(paramsOf(tangerine.required, { chat: '-100', reply_to: '5' })).toEqual({ chat: -100, reply_to: 5 });
	});
});
