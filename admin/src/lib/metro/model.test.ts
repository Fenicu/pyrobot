import { describe, expect, it } from 'vitest';
import type { MetroRunDetail, MetroRunSummary } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { eventCounts, frameAt, mapOf, outcomeOf, summarize } from './model';

const run = fixture<MetroRunDetail>('metro_run_1');
const runs = fixture<{ items: MetroRunSummary[] }>('metro_runs').items;

describe('забег метро 27.09', () => {
	it('карта: клетки, границы, путь, выход', () => {
		const m = mapOf(run);
		expect(m.cells).toHaveLength(285);
		expect([m.minRow, m.minCol, m.rows, m.cols]).toEqual([-2, -5, 19, 15]);
		expect(m.path).toHaveLength(198);
		expect(m.exit).toEqual([14, 1]);
		expect(m.visitedCount).toBe(95);
		expect(m.cells.find((c) => c.row === 0 && c.col === 2)?.sym).toBe('#');
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
