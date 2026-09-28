import { call, type Api } from '$lib/api/client';

/** Поля метрик (`METRIC_FIELDS` движка) с подписями. */
export const METRICS: { key: string; label: string }[] = [
	{ key: 'money', label: '💵 деньги' },
	{ key: 'exp', label: '💡 опыт' },
	{ key: 'motivation', label: '🔥 мотивация' },
	{ key: 'stamina', label: '🔋 выносливость' },
	{ key: 'knowledge', label: '📚 знания' },
	{ key: 'raw', label: '🔩 сырьё' },
	{ key: 'details', label: '⚙️ детали' },
	{ key: 'books', label: '📒 книги' },
	{ key: 'glory', label: '🏆 слава' },
	{ key: 'level', label: 'уровень' }
];

export type Point = [string, number];

/** Ряды метрик окна: точки `[момент, значение]` по полю и значение на начало окна. */
export interface MetricsData {
	series: Record<string, Point[]>;
	initial: Record<string, Point>;
}

interface RawPage {
	series: Record<string, unknown[]>;
	initial: Record<string, unknown>;
	next_cursor: string | null;
}

function point(raw: unknown): Point | null {
	return Array.isArray(raw) && typeof raw[0] === 'string' && typeof raw[1] === 'number'
		? [raw[0], raw[1]]
		: null;
}

export interface Window {
	from: Date;
	to: Date;
}

export interface LoadOptions {
	signal?: AbortSignal;
	/** После каждой страницы: сколько страниц и точек уже получено. */
	onProgress?: (pages: number, points: number) => void;
}

/** Все страницы окна до `next_cursor = null`: `initial` — с первой, ряды склеиваются. */
export async function loadMetrics(
	api: Api,
	win: Window,
	fields: string[],
	{ signal, onProgress }: LoadOptions = {}
): Promise<MetricsData> {
	const out: MetricsData = { series: {}, initial: {} };
	let cursor: string | null = null;
	let points = 0;
	for (let page = 0; ; page++) {
		signal?.throwIfAborted();
		const res: RawPage = await call(
			api.GET('/api/v1/metrics', {
				params: {
					query: {
						from: win.from.toISOString(),
						to: win.to.toISOString(),
						fields: fields.join(','),
						...(cursor ? { cursor } : {})
					}
				},
				signal
			})
		);
		if (page === 0) {
			for (const [key, raw] of Object.entries(res.initial)) {
				const p = point(raw);
				if (p) out.initial[key] = p;
			}
		}
		for (const [key, list] of Object.entries(res.series)) {
			const target = (out.series[key] ??= []);
			for (const raw of list) {
				const p = point(raw);
				if (p) {
					target.push(p);
					points += 1;
				}
			}
		}
		onProgress?.(page + 1, points);
		if (res.next_cursor === null) return out;
		if (res.next_cursor === cursor) throw new Error('курсор метрик не сдвигается');
		cursor = res.next_cursor;
	}
}

/** Ступенчатый ряд для графика: значение на начало окна, точки изменений и продление последнего
 * значения до конца окна (метрика пишется только при изменении). x — секунды Unix. */
export function stepSeries(data: MetricsData, key: string, win: Window): [number[], number[]] {
	const xs: number[] = [];
	const ys: number[] = [];
	const start = win.from.getTime() / 1000;
	const end = win.to.getTime() / 1000;
	const initial = data.initial[key];
	if (initial) {
		xs.push(start);
		ys.push(initial[1]);
	}
	for (const [at, value] of data.series[key] ?? []) {
		const t = new Date(at).getTime() / 1000;
		if (Number.isNaN(t)) continue;
		xs.push(t);
		ys.push(value);
	}
	const last = ys.at(-1);
	if (last !== undefined && (xs.at(-1) ?? end) < end) {
		xs.push(end);
		ys.push(last);
	}
	return [xs, ys];
}
