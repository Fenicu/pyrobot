import type { MetroRunDetail, MetroRunSummary } from '$lib/api/types';
import { CURRENCY } from '$lib/util/game';

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

/** Сводка по списку забегов: p90 длительности завершённых, среднее время шага, шагов на
 * посещённую клетку (по забегам с картой; у списка сервера до 0.4 `visited` нет — null), исходы
 * числом и долями. */
export function summarize(runs: MetroRunSummary[]) {
	const done = runs.filter((r) => r.status === 'done');
	const steps = runs.map((r) => r.step_s).filter((s): s is number => typeof s === 'number');
	const mapped = runs.filter((r) => typeof r.visited === 'number' && r.visited > 0);
	const cells = mapped.reduce((a, r) => a + r.visited, 0);
	const count = (o: Outcome) => runs.filter((r) => outcomeOf(r) === o).length;
	const share = (n: number) => (runs.length ? n / runs.length : null);
	const [self, ejected, stopped] = [count('self'), count('ejected'), count('stopped')];
	return {
		total: runs.length,
		p90DurationS: percentile(
			done.map((r) => r.duration_s),
			0.9
		),
		meanStepS: steps.length ? steps.reduce((a, b) => a + b, 0) / steps.length : null,
		stepsPerCell: cells > 0 ? mapped.reduce((a, r) => a + r.steps, 0) / cells : null,
		self,
		ejected,
		stopped,
		selfShare: share(self),
		ejectedShare: share(ejected),
		stoppedShare: share(stopped)
	};
}

export interface MapModel {
	/** Рамка карты: проходы, путь и события с одной клеткой запаса — без сплошных стен вокруг. */
	minRow: number;
	minCol: number;
	rows: number;
	cols: number;
	cells: { row: number; col: number; sym: string; visited: boolean }[];
	/** Проходы: клетки не-стены и клетки пути (путь — всегда проход, даже если сетка не сохранена). */
	tunnels: { row: number; col: number; sym: string }[];
	path: Pos[];
	events: MetroEvent[];
	vitals: Vitals[];
	exit: Pos | null;
	visitedCount: number;
	/** Шаг, на котором клетка впервые попала в окно кадра (5×5 вокруг персонажа на пути). */
	seenAt: Map<string, number>;
	/** Шаг первого прихода в клетку. */
	visitedAt: Map<string, number>;
	/** Аптечки: шаг и клетка, где их стало меньше. */
	heals: { pos: Pos; step: number }[];
}

/** Окно кадра карты — 5×5 вокруг персонажа: столько клеток каждый кадр открывает. */
export const VIEW_RADIUS = 2;

export const cellKey = (row: number, col: number) => `${row},${col}`;

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
	const tunnels = new Map<string, { row: number; col: number; sym: string }>();
	for (const c of cells) if (c.sym !== '#') tunnels.set(cellKey(c.row, c.col), { row: c.row, col: c.col, sym: c.sym });
	for (const [row, col] of path) if (!tunnels.has(cellKey(row, col))) tunnels.set(cellKey(row, col), { row, col, sym: '.' });
	// Рамка — по проходам и событиям с клеткой запаса: стены за ней — сплошной камень.
	const open: Pos[] = [...[...tunnels.values()].map((c) => [c.row, c.col] as Pos), ...events.map((e) => e.pos)];
	const rowsOf = open.map((p) => p[0]);
	const colsOf = open.map((p) => p[1]);
	const minRow = open.length ? Math.min(...rowsOf) - 1 : 0;
	const minCol = open.length ? Math.min(...colsOf) - 1 : 0;
	const summaryExit = run.summary?.exit;
	const exitCell = cells.find((c) => c.sym === 'E');
	const seenAt = new Map<string, number>();
	const visitedAt = new Map<string, number>();
	path.forEach(([r, c], step) => {
		if (!visitedAt.has(cellKey(r, c))) visitedAt.set(cellKey(r, c), step);
		for (let dr = -VIEW_RADIUS; dr <= VIEW_RADIUS; dr++) {
			for (let dc = -VIEW_RADIUS; dc <= VIEW_RADIUS; dc++) {
				const key = cellKey(r + dr, c + dc);
				if (!seenAt.has(key)) seenAt.set(key, step);
			}
		}
	});
	const heals = vitals.flatMap((v, i) =>
		i > 0 && v.packs < vitals[i - 1]!.packs && isPos(v.pos) ? [{ pos: v.pos, step: v.step }] : []
	);
	return {
		minRow,
		minCol,
		rows: open.length ? Math.max(...rowsOf) + 1 - minRow + 1 : 0,
		cols: open.length ? Math.max(...colsOf) + 1 - minCol + 1 : 0,
		cells,
		tunnels: [...tunnels.values()],
		path,
		events,
		vitals,
		exit: isPos(summaryExit) ? summaryExit : exitCell ? [exitCell.row, exitCell.col] : null,
		visitedCount: visited.size,
		seenAt,
		visitedAt,
		heals
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

export const EVENT_TEXT: Record<string, string> = {
	metro_loot: 'лут',
	metro_chest: 'сундук',
	metro_chest_opened: 'сундук открыт',
	metro_npc: 'NPC',
	metro_fight: 'бой',
	metro_exit: 'выход',
	leave: 'уход к выходу',
	metro_finished: 'финиш',
	metro_collapsed: 'выброс обвалом',
	metro_entrance: 'вход',
	metro_buffs: 'бафы'
};

export const LEAVE_REASON: Record<string, string> = {
	explored: 'всё обошёл',
	deadline: 'не успеть до битвы',
	early_exit: 'досрочный выход',
	no_stamina: '🔋 0 и нет аптечек'
};

/** «💵 71 ⚙️ 9 ⚪ 1». */
export function lootText(loot: unknown): string {
	if (typeof loot !== 'object' || loot === null) return '';
	return Object.entries(loot as Record<string, unknown>)
		.map(([k, v]) => `${CURRENCY[k] ?? k} ${String(v)}`)
		.join(' ');
}

/** Событие забега словами: «бой с 👨Варвара (71): победа, +💵 71 ⚙️ 9», «сундук-ловушка: стрела». */
export function eventText(e: MetroEvent): string {
	const loot = lootText(e.loot);
	switch (e.kind) {
		case 'metro_loot':
			return `находка: +${CURRENCY[String(e.item)] ?? String(e.item)} ${String(e.amount ?? '')}`.trim();
		case 'metro_npc':
			return `NPC (${e.strength === 'high' ? 'сильный' : 'слабый'})`;
		case 'metro_fight':
			return `бой${typeof e.enemy === 'string' ? ` с ${e.enemy}` : ''}: ${e.won ? 'победа' : 'поражение'}${loot ? `, +${loot}` : ''}`;
		case 'metro_chest_opened':
			if (e.result === 'arrow') return 'сундук-ловушка: стрела, 🔋 до нуля';
			if (e.result === 'grenade') return 'сундук-ловушка: граната, половина найденного потеряна';
			return `сундук-тайник${loot ? `: +${loot}` : ''}`;
		case 'metro_finished':
			return `финиш${loot ? `: +${loot}` : ''}`;
		case 'leave':
			return `уход к выходу${typeof e.reason === 'string' ? `: ${LEAVE_REASON[e.reason] ?? e.reason}` : ''}`;
		case 'metro_buffs':
			return Array.isArray(e.bought) && e.bought.length ? `бафы: ${e.bought.join(', ')}` : 'бафы';
		case 'heal':
			return 'аптечка';
		default:
			return EVENT_TEXT[e.kind] ?? e.kind;
	}
}

/** Значок события на карте; null — без значка (выход, финиш, решения бота). */
export function eventIcon(e: MetroEvent): string | null {
	switch (e.kind) {
		case 'metro_loot':
			return CURRENCY[String(e.item)] ?? '✨';
		case 'metro_fight':
			return e.won ? '⚔️' : '💀';
		case 'metro_npc':
			return '👤';
		case 'metro_chest':
			return '📦';
		case 'metro_chest_opened':
			return e.result === 'arrow' ? '🏹' : e.result === 'grenade' ? '💥' : '📦';
		case 'heal':
			return '❤️';
		default:
			return null;
	}
}

/** Что важнее показать в клетке: бой — сундук — NPC — аптечка — находка. */
const PRIORITY: Record<string, number> = {
	metro_fight: 6,
	metro_chest_opened: 5,
	metro_chest: 4,
	metro_npc: 3,
	heal: 2,
	metro_loot: 1
};

export interface CellMark {
	pos: Pos;
	kind: string;
	icon: string;
	/** Сундук не открыт, NPC без боя — приглушённо. */
	dim: boolean;
	/** Все события клетки к шагу, по строке: «шаг 39 · бой …». */
	title: string;
}

/** События забега и аптечки (`kind: 'heal'`) по шагам. */
export function timeline(model: MapModel): MetroEvent[] {
	const heals: MetroEvent[] = model.heals.map((h) => ({ kind: 'heal', pos: h.pos, step: h.step }));
	return [...model.events, ...heals].toSorted((a, b) => a.step - b.step);
}

/** Значки клеток к шагу — только случившееся (будущее не показывается): главное событие клетки,
 * в подсказке — все её события. */
export function cellMarks(model: MapModel, step: number): CellMark[] {
	const byCell = new Map<string, MetroEvent[]>();
	for (const e of timeline(model)) {
		if (e.step > step || eventIcon(e) === null) continue;
		const key = cellKey(e.pos[0], e.pos[1]);
		byCell.set(key, [...(byCell.get(key) ?? []), e]);
	}
	return [...byCell.values()].map((list) => {
		const top = list.reduce((a, b) => ((PRIORITY[b.kind] ?? 0) >= (PRIORITY[a.kind] ?? 0) ? b : a));
		const has = (kind: string) => list.some((e) => e.kind === kind);
		return {
			pos: top.pos,
			kind: top.kind,
			icon: eventIcon(top)!,
			dim: (top.kind === 'metro_chest' && !has('metro_chest_opened')) || (top.kind === 'metro_npc' && !has('metro_fight')),
			title: list.map((e) => `шаг ${e.step} · ${eventText(e)}`).join('\n')
		};
	});
}

/** Число событий по видам: «лут ×14, сундук ×3 …». */
export function eventCounts(events: MetroEvent[]): [string, number][] {
	const counts = new Map<string, number>();
	for (const e of events) counts.set(e.kind, (counts.get(e.kind) ?? 0) + 1);
	return [...counts.entries()];
}
