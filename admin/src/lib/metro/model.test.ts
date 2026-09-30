import { describe, expect, it } from 'vitest';
import type { MetroRunDetail, MetroRunSummary } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { cellMarks, eventCounts, eventIcon, eventText, frameAt, mapOf, outcomeOf, summarize, timeline, type MetroEvent } from './model';

const run = fixture<MetroRunDetail>('metro_run_1');
const runs = fixture<{ items: MetroRunSummary[] }>('metro_runs').items;

describe('забег метро 27.09', () => {
	it('карта: клетки, границы, путь, выход', () => {
		const m = mapOf(run);
		expect(m.cells).toHaveLength(285);
		expect(m.tunnels).toHaveLength(95);
		// Рамка — проходы с клеткой камня вокруг, без двух рядов стен сетки.
		expect([m.minRow, m.minCol, m.rows, m.cols]).toEqual([-1, -4, 17, 13]);
		expect(m.path).toHaveLength(198);
		expect(m.exit).toEqual([14, 1]);
		expect(m.visitedCount).toBe(95);
		expect(m.cells.find((c) => c.row === 0 && c.col === 2)?.sym).toBe('#');
	});

	it('туман и пройденное: окно кадра 5×5 открывает клетки, приход — отдельно', () => {
		const m = mapOf(run);
		expect(m.seenAt.get('0,0')).toBe(0);
		expect(m.seenAt.get('2,-2')).toBe(0);
		expect(m.seenAt.has('3,0')).toBe(true);
		expect(m.seenAt.get('14,1')).toBe(83);
		expect(m.visitedAt.get('14,1')).toBe(173);
		expect(m.tunnels.filter((c) => m.seenAt.get(`${c.row},${c.col}`)! <= 0)).toHaveLength(9);
		expect(m.heals).toEqual([{ pos: [12, 3], step: 193 }]);
		// Путь — проход, даже если сетки нет.
		const bare = mapOf({ ...run, grid: {}, path: [[0, 0], [0, 1]] });
		expect(bare.tunnels).toEqual([{ row: 0, col: 0, sym: '.' }, { row: 0, col: 1, sym: '.' }]);
	});

	it('значки клеток: только случившееся, главное событие клетки, в подсказке — все', () => {
		const m = mapOf(run);
		const at = (step: number, pos: string) => cellMarks(m, step).find((x) => x.pos.join(',') === pos);
		expect(at(37, '6,0')).toBeUndefined();
		expect(at(38, '6,0')).toMatchObject({ kind: 'metro_chest_opened', icon: '📦', dim: false });
		expect(at(39, '6,-1')).toEqual({
			pos: [6, -1],
			kind: 'metro_fight',
			icon: '⚔️',
			dim: false,
			title: 'шаг 39 · NPC (слабый)\nшаг 39 · бой с 👨Продаваном 👨Варвара (71): победа, +💵 71 ⚙️ 9 ⚪ 1'
		});
		expect(at(197, '12,3')).toMatchObject({ kind: 'heal', icon: '❤️' });
		// Выход и финиш — без значка события: у выхода свой 🚪.
		expect(at(197, '14,1')).toBeUndefined();
		expect(cellMarks(m, 197)).toHaveLength(21);
		expect(timeline(m).map((e) => e.kind).slice(-4)).toEqual(['leave', 'heal', 'metro_exit', 'metro_finished']);
		// Сундук не открыт, NPC без боя — приглушённо.
		const lone = mapOf({
			...run,
			events: [
				{ kind: 'metro_chest', pos: [0, 0], step: 1 },
				{ kind: 'metro_npc', pos: [0, -1], step: 2, strength: 'high' }
			]
		});
		expect(cellMarks(lone, 5).map((x) => [x.icon, x.dim])).toEqual([
			['📦', true],
			['👤', true]
		]);
	});

	it('события словами и значком', () => {
		const e = (kind: string, rest: Partial<MetroEvent> = {}): MetroEvent => ({ kind, pos: [0, 0], step: 1, ...rest });
		expect([eventIcon(e('metro_loot', { item: 'money' })), eventText(e('metro_loot', { item: 'money', amount: 72 }))]).toEqual([
			'💵',
			'находка: +💵 72'
		]);
		expect(eventIcon(e('metro_loot', { item: 'новое' }))).toBe('✨');
		expect([eventIcon(e('metro_fight', { won: false })), eventText(e('metro_fight', { won: false }))]).toEqual([
			'💀',
			'бой: поражение'
		]);
		expect([eventIcon(e('metro_chest_opened', { result: 'arrow' })), eventText(e('metro_chest_opened', { result: 'arrow' }))]).toEqual([
			'🏹',
			'сундук-ловушка: стрела, 🔋 до нуля'
		]);
		expect(eventIcon(e('metro_chest_opened', { result: 'grenade' }))).toBe('💥');
		expect(eventText(e('leave', { reason: 'deadline' }))).toBe('уход к выходу: не успеть до битвы');
		expect([eventIcon(e('metro_exit')), eventText(e('metro_exit'))]).toEqual([null, 'выход']);
		expect([eventText(e('metro_buffs', { bought: ['fastMove', 'strong'] })), eventText(e('metro_buffs', { bought: [] }))]).toEqual([
			'бафы: fastMove, strong',
			'бафы'
		]);
	});

	it('кадр шага: клетка, 🔋 и аптечки, события к шагу', () => {
		const m = mapOf(run);
		expect(frameAt(m, 120)).toMatchObject({ pos: [10, -2], vitals: { stamina: 98, packs: 7 } });
		expect(frameAt(m, 0).events).toEqual([]);
		expect(frameAt(m, 39).events.map((e) => e.kind)).toContain('metro_fight');
		expect(frameAt(m, 10_000).pos).toEqual([14, 1]);
	});

	it('исход, сводка и события по видам', () => {
		expect(outcomeOf(run)).toBe('self');
		expect(outcomeOf({ ...run, summary: { ...run.summary, mode: 'explore' } })).toBe('ejected');
		expect(outcomeOf({ ...run, status: 'failed' })).toBe('stopped');
		expect(summarize(runs)).toMatchObject({ total: 1, p90DurationS: 543.496275, self: 1, ejected: 0 });
		expect(Object.fromEntries(eventCounts(mapOf(run).events))).toMatchObject({
			metro_loot: 14,
			metro_fight: 3,
			metro_npc: 3,
			metro_chest: 3,
			metro_exit: 2
		});
	});

	it('сводка: шагов на клетку по посещённым клеткам и исходы долями', () => {
		const base = runs[0]!;
		const r = (steps: number, visited: number, status: string, mode: string) => ({
			...base,
			steps,
			visited,
			status,
			summary: { ...base.summary, mode }
		});
		const stats = summarize([r(100, 50, 'done', 'leave'), r(60, 20, 'done', 'explore'), r(10, 0, 'failed', 'explore'), r(30, 10, 'done', 'leave')]);
		// Забег без посещённых клеток (упал до карты) в «шагов на клетку» не входит.
		expect(stats.stepsPerCell).toBeCloseTo(190 / 80);
		expect([stats.self, stats.ejected, stats.stopped]).toEqual([2, 1, 1]);
		expect([stats.selfShare, stats.ejectedShare, stats.stoppedShare]).toEqual([0.5, 0.25, 0.25]);
		// Список сервера до 0.4 — без visited.
		expect(summarize(runs).stepsPerCell).toBeNull();
		expect(summarize([]).selfShare).toBeNull();
	});

	it('пустой и незавершённый забег', () => {
		const empty = mapOf({ grid: {}, path: [], events: [], vitals: [], summary: {} });
		expect([empty.cells, empty.path, empty.exit, empty.rows]).toEqual([[], [], null, 0]);
		expect(frameAt(empty, 5)).toEqual({ pos: null, vitals: null, events: [] });
		const partial = mapOf({ ...run, path: run.path.slice(0, 10), vitals: run.vitals.slice(0, 10), grid: { cells: { '0,0': '.' } }, summary: {} });
		expect(partial.exit).toBeNull();
		expect(frameAt(partial, 50).pos).toEqual(run.path[9]);
	});
});
