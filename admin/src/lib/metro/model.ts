import type { MetroRunDetail, MetroRunSummary } from '$lib/api/types';

export type Pos = [number, number];

/** Событие забега: `pos` — [ряд, колонка], `step` — номер шага. */
export interface MetroEvent {
	kind: string;
	pos: Pos;
	step: number;
	[key: string]: unknown;
}

export interface Vitals {
	pos: Pos;
	step: number;
	stamina: number;
	packs: number;
}

export type Outcome = 'self' | 'ejected' | 'stopped';

/** Исход забега для списка: вышел сам (ушёл к выходу по решению), выброс игрой или остановка. */
export function outcomeOf(run: MetroRunSummary): Outcome {
	if (run.status !== 'done') return 'stopped';
	return run.summary?.mode === 'leave' ? 'self' : 'ejected';
}

export const OUTCOME_TEXT: Record<Outcome, string> = {
	self: 'вышел сам',
	ejected: 'выброс',
	stopped: 'остановлен'
};

function percentile(values: number[], p: number): number | null {
	if (values.length === 0) return null;
	const sorted = [...values].sort((a, b) => a - b);
	const i = Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1));
	return sorted[i] ?? null;
}

/** Сводка по списку забегов: p90 длительности завершённых, среднее время шага, доли исходов. */
export function summarize(runs: MetroRunSummary[]) {
	const done = runs.filter((r) => r.status === 'done');
	const steps = runs.map((r) => r.step_s).filter((s): s is number => typeof s === 'number');
	const count = (o: Outcome) => runs.filter((r) => outcomeOf(r) === o).length;
	return {
		total: runs.length,
		p90DurationS: percentile(
			done.map((r) => r.duration_s),
			0.9
		),
		meanStepS: steps.length ? steps.reduce((a, b) => a + b, 0) / steps.length : null,
		self: count('self'),
		ejected: count('ejected'),
		stopped: count('stopped')
	};
}

export interface MapModel {
	minRow: number;
	minCol: number;
	rows: number;
	cols: number;
	cells: { row: number; col: number; sym: string; visited: boolean }[];
	path: Pos[];
	events: MetroEvent[];
	vitals: Vitals[];
	exit: Pos | null;
	visitedCount: number;
}

const isPos = (v: unknown): v is Pos =>
	Array.isArray(v) && v.length === 2 && typeof v[0] === 'number' && typeof v[1] === 'number';

/** Карта из деталей забега: клетки `grid.cells` ("ряд,колонка" → символ), посещённые, путь,
 * события и 🔋 по шагам; пустой или незавершённый забег даёт пустую модель. */
export function mapOf(run: Pick<MetroRunDetail, 'grid' | 'path' | 'events' | 'vitals' | 'summary'>): MapModel {
	const grid = (run.grid ?? {}) as { cells?: Record<string, string>; visited?: unknown[] };
	const visited = new Set((grid.visited ?? []).filter(isPos).map(([r, c]) => `${r},${c}`));
	const cells = Object.entries(grid.cells ?? {}).flatMap(([key, sym]) => {
		const [r, c] = key.split(',').map(Number);
		return Number.isFinite(r) && Number.isFinite(c)
			? [{ row: r!, col: c!, sym, visited: visited.has(key) }]
			: [];
	});
	const path = (run.path ?? []).filter(isPos);
	const events = (run.events ?? []).filter(
		(e): e is MetroEvent => typeof e === 'object' && e !== null && isPos((e as MetroEvent).pos)
	);
	const vitals = (run.vitals ?? []).filter(
		(v): v is Vitals => typeof v === 'object' && v !== null && typeof (v as Vitals).step === 'number'
	);
	const all: Pos[] = [...cells.map((c) => [c.row, c.col] as Pos), ...path, ...events.map((e) => e.pos)];
	const rowsOf = all.map((p) => p[0]);
	const colsOf = all.map((p) => p[1]);
	const minRow = all.length ? Math.min(...rowsOf) : 0;
	const minCol = all.length ? Math.min(...colsOf) : 0;
	const summaryExit = run.summary?.exit;
	const exitCell = cells.find((c) => c.sym === 'E');
	return {
		minRow,
		minCol,
		rows: all.length ? Math.max(...rowsOf) - minRow + 1 : 0,
		cols: all.length ? Math.max(...colsOf) - minCol + 1 : 0,
		cells,
		path,
		events,
		vitals,
		exit: isPos(summaryExit) ? summaryExit : exitCell ? [exitCell.row, exitCell.col] : null,
		visitedCount: visited.size
	};
}

/** Состояние на шаге: клетка, 🔋 и аптечки (последняя запись не позже шага). */
export function frameAt(model: MapModel, step: number) {
	const pos = model.path[Math.min(step, model.path.length - 1)] ?? null;
	let vitals: Vitals | null = null;
	for (const v of model.vitals) {
		if (v.step <= step) vitals = v;
		else break;
	}
	return { pos, vitals, events: model.events.filter((e) => e.step <= step) };
}

export type EventTone = 'loot' | 'fight' | 'chest' | 'exit' | 'other';

export function eventTone(kind: string): EventTone {
	if (kind === 'metro_loot') return 'loot';
	if (kind === 'metro_fight' || kind === 'metro_npc') return 'fight';
	if (kind.startsWith('metro_chest')) return 'chest';
	if (kind === 'metro_exit' || kind === 'metro_finished' || kind === 'leave') return 'exit';
	return 'other';
}

export const EVENT_TEXT: Record<string, string> = {
	metro_loot: 'лут',
	metro_chest: 'сундук',
	metro_chest_opened: 'сундук открыт',
	metro_npc: 'NPC',
	metro_fight: 'бой',
	metro_exit: 'выход',
	leave: 'уход к выходу',
	metro_finished: 'финиш'
};

/** Число событий по видам: «лут ×14, сундук ×3 …». */
export function eventCounts(events: MetroEvent[]): [string, number][] {
	const counts = new Map<string, number>();
	for (const e of events) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1);
	return [...counts.entries()];
}
