import { fmtNum, TZ } from '$lib/util/format';
import { axisNumbers } from './axis';
import type { EChartsCoreOption } from './echarts';
import type { Marker } from './events';

/** Ряд графика: x — секунды Unix, y — значения (ступенчатый, см. `stepSeries`). */
export type Series = [number[], number[]];

// МСК — UTC+3 без перехода на летнее время: ECharts рисует время в UTC (`useUTC`), а точки
// сдвинуты на +3 ч — подписи оси выходят по Москве в любом часовом поясе браузера.
const MSK_MS = 3 * 3_600_000;
export const toChart = (sec: number) => sec * 1000 + MSK_MS;
export const fromChart = (ms: number) => (ms - MSK_MS) / 1000;

/** Цвета темы (CSS-переменные) — график перекрашивается при смене темы. */
export interface Palette {
	accent: string;
	text: string;
	muted: string;
	faint: string;
	grid: string;
	surface: string;
	line: string;
}

export function readPalette(): Palette {
	const css = (name: string, fallback: string) =>
		getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
	return {
		accent: css('--accent', '#4f8cff'),
		text: css('--fg', '#e6e6ea'),
		muted: css('--fg-muted', '#a0a0aa'),
		faint: css('--fg-faint', '#74747e'),
		grid: css('--line-soft', '#2a2a30'),
		surface: css('--surface', '#1b1b1f'),
		line: css('--line', '#34343c')
	};
}

/** Значение ступенчатого ряда в момент `t`: последняя точка не позже него. */
export function valueAt([xs, ys]: Series, t: number): number | null {
	let lo = 0;
	let hi = xs.length - 1;
	let found = -1;
	while (lo <= hi) {
		const mid = (lo + hi) >> 1;
		if (xs[mid]! <= t) {
			found = mid;
			lo = mid + 1;
		} else {
			hi = mid - 1;
		}
	}
	return found < 0 ? null : ys[found]!;
}

/** Значения, которые видны на участке [from, to]: действующее в начале и изменения внутри. */
export function visibleValues(data: Series, from: number, to: number): number[] {
	const [xs, ys] = data;
	const out: number[] = [];
	const first = valueAt(data, from);
	if (first !== null) out.push(first);
	xs.forEach((x, i) => {
		if (x > from && x <= to) out.push(ys[i]!);
	});
	return out;
}

export interface Scale {
	min: number;
	max: number;
	interval: number;
	/** Подписи делений от `min` с шагом `interval` — с одинаковым числом знаков (`axisNumbers`). */
	labels: string[];
}

const clean = (v: number) => Number(v.toPrecision(12));

/** Круглые деления оси значений (шаг 1, 2, 2.5 или 5 × 10ⁿ, около `ticks` штук). */
export function niceScale(values: number[], ticks = 4): Scale | null {
	if (values.length === 0) return null;
	let lo = Math.min(...values);
	let hi = Math.max(...values);
	if (lo === hi) {
		const pad = Math.abs(lo) * 0.01 || 1;
		lo -= pad;
		hi += pad;
	}
	const raw = (hi - lo) / ticks;
	const mag = 10 ** Math.floor(Math.log10(raw));
	const norm = raw / mag;
	const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
	const interval = clean(step * mag);
	const min = clean(Math.floor(lo / interval) * interval);
	const max = clean(Math.ceil(hi / interval) * interval);
	const count = Math.round((max - min) / interval);
	const splits = Array.from({ length: count + 1 }, (_, i) => clean(min + i * interval));
	return { min, max, interval, labels: axisNumbers(splits) };
}

/** Метки участка; значок — если не ближе `gapPx` к предыдущему нарисованному. */
export function markersInView(
	marks: Marker[],
	from: number,
	to: number,
	plotWidth: number,
	gapPx = 16
): (Marker & { showIcon: boolean })[] {
	const perPx = (to - from) / Math.max(plotWidth, 1);
	let last = Number.NEGATIVE_INFINITY;
	return marks
		.filter((m) => m.x >= from && m.x <= to)
		.map((m) => {
			const showIcon = (m.x - last) / perPx >= gapPx;
			if (showIcon) last = m.x;
			return { ...m, showIcon };
		});
}

const moment = new Intl.DateTimeFormat('ru-RU', {
	timeZone: TZ,
	day: '2-digit',
	month: '2-digit',
	hour: '2-digit',
	minute: '2-digit'
});

/** «27.09 21:45» по Москве. */
export function fmtWhen(sec: number): string {
	return moment.format(new Date(sec * 1000)).replace(',', '');
}

const escape = (s: string) =>
	s.replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]!);

/** Подсказка в момент `t`: время, точное значение и метки событий рядом (в пределах `radius` с). */
export function tooltipHtml(name: string, data: Series, marks: Marker[], t: number, radius: number): string {
	const value = valueAt(data, t);
	const near = marks.filter((m) => Math.abs(m.x - t) <= radius);
	return [
		`<div style="opacity:.7">${fmtWhen(t)}</div>`,
		`<div>${escape(name)}: <b>${value === null ? '—' : fmtNum(value)}</b></div>`,
		...near.map((m) => `<div>${m.icon} ${escape(m.label)} · ${fmtWhen(m.x).slice(-5)}</div>`)
	].join('');
}

export interface ChartParams {
	name: string;
	data: Series;
	markers: Marker[];
	/** Окно графика, секунды: общее у всех графиков, чтобы время совпадало по вертикали. */
	range: [number, number];
	width: number;
	colors: Palette;
	/** Видимый участок: меняется выделением, по нему подсказка ищет метки рядом с курсором. */
	view: { range: [number, number] };
	/** Момент курсора другого графика (единицы оси времени), когда подсказку показывает связка. */
	followed?: () => number | null;
}

/** Ширина области графика без подписей оси значений — для прореживания значков меток и поиска
 * меток рядом с курсором; ширина ещё не известна (0) — обычная ширина графика. */
const plotWidth = (width: number) => (width > 64 ? width - 64 : 600);

// Подписи оси времени: 24 часа и дд.мм, на смене дня — дата.
const TIME_LABELS = {
	year: '{yyyy}',
	month: '{MM}.{yyyy}',
	day: '{dd}.{MM}',
	hour: '{HH}:{mm}',
	minute: '{HH}:{mm}',
	second: '{HH}:{mm}:{ss}',
	millisecond: '{HH}:{mm}:{ss}',
	none: '{HH}:{mm}'
};

function yAxis(scale: Scale | null, c: Palette) {
	return {
		type: 'value',
		scale: true,
		...(scale ? { min: scale.min, max: scale.max, interval: scale.interval } : {}),
		axisLabel: {
			color: c.muted,
			formatter: (v: number) =>
				(scale ? scale.labels[Math.round((v - scale.min) / scale.interval)] : undefined) ??
				axisNumbers([v])[0]!
		},
		splitLine: { lineStyle: { color: c.grid } }
	};
}

function markLine(p: ChartParams, [from, to]: [number, number]) {
	const c = p.colors;
	return {
		silent: true,
		symbol: ['none', 'none'],
		animation: false,
		lineStyle: { color: c.faint, type: 'dashed', width: 1 },
		label: { position: 'end', distance: 2, fontSize: 12 },
		emphasis: { disabled: true },
		data: markersInView(p.markers, from, to, plotWidth(p.width)).map((m) => ({
			xAxis: toChart(m.x),
			label: { show: m.showIcon, formatter: m.icon }
		}))
	};
}

function withAlpha(color: string, alpha: number): string {
	const hex = /^#([0-9a-f]{6})$/i.exec(color)?.[1];
	if (!hex) return color;
	const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
	return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Настройки ECharts: ступенчатая линия с заливкой, подсказка с точным значением под курсором,
 * вертикали событий со значками, выделение участка мышью (панель справа сверху: выделить, назад). */
export function chartOption(p: ChartParams): EChartsCoreOption {
	const c = p.colors;
	const [from, to] = p.range;
	const [xs, ys] = p.data;
	return {
		useUTC: true,
		// Без анимаций: перерисовка ступенчатого ряда с заливкой ломает её нижний край, а подписи осей
		// при двух обновлениях подряд (выделение участка и пересчёт оси) остаются от прежней разметки.
		animation: false,
		textStyle: { fontFamily: 'system-ui, sans-serif', fontSize: 12 },
		// Сверху две строки: панель (выделить, назад) и под ней значки меток.
		grid: {
			left: 4,
			right: 12,
			top: 42,
			bottom: 4,
			outerBoundsMode: 'same',
			outerBoundsContain: 'axisLabel'
		},
		toolbox: {
			right: 4,
			top: 0,
			padding: 0,
			itemSize: 13,
			itemGap: 8,
			iconStyle: { borderColor: c.faint },
			emphasis: { iconStyle: { borderColor: c.accent, textFill: c.text } },
			feature: {
				// Точки за краями участка не отбрасываются: ступенька, начатая раньше, тянется до края.
				dataZoom: {
					yAxisIndex: 'none',
					filterMode: 'none',
					title: { zoom: 'выделить участок', back: 'назад' }
				}
			}
		},
		tooltip: {
			trigger: 'axis',
			confine: true,
			transitionDuration: 0,
			backgroundColor: c.surface,
			borderColor: c.line,
			borderWidth: 1,
			padding: [6, 8],
			textStyle: { color: c.text, fontSize: 12 },
			extraCssText: 'border-radius: 6px; box-shadow: 0 4px 14px rgb(0 0 0 / 0.25);',
			axisPointer: {
				type: 'line',
				snap: false,
				lineStyle: { color: c.muted, width: 1 },
				label: { show: false }
			},
			formatter: (params: { axisValue?: number | string } | { axisValue?: number | string }[]) => {
				const first = Array.isArray(params) ? params[0] : params;
				const t = fromChart(p.followed?.() ?? Number(first?.axisValue));
				const [a, b] = p.view.range;
				const radius = ((b - a) / plotWidth(p.width)) * 8;
				return tooltipHtml(p.name, p.data, p.markers, t, radius);
			}
		},
		xAxis: {
			type: 'time',
			min: toChart(from),
			max: toChart(to),
			axisLine: { lineStyle: { color: c.line } },
			axisTick: { show: false },
			axisLabel: { color: c.muted, hideOverlap: true, formatter: TIME_LABELS },
			splitLine: { show: false }
		},
		yAxis: yAxis(niceScale(visibleValues(p.data, from, to)), c),
		series: [
			{
				type: 'line',
				name: p.name,
				step: 'end',
				// Без точек, в том числе под курсором: подсветилась бы ближайшая точка ряда, а не момент
				// курсора, по которому считается подсказка.
				symbol: 'none',
				data: xs.map((x, i) => [toChart(x), ys[i]]),
				lineStyle: { width: 2, color: c.accent },
				itemStyle: { color: c.accent },
				areaStyle: {
					origin: 'start',
					color: {
						type: 'linear',
						x: 0,
						y: 0,
						x2: 0,
						y2: 1,
						colorStops: [
							{ offset: 0, color: withAlpha(c.accent, 0.28) },
							{ offset: 1, color: withAlpha(c.accent, 0) }
						]
					}
				},
				emphasis: { disabled: true },
				markLine: markLine(p, p.range)
			}
		]
	};
}

/** Выделен участок [from, to]: ось значений — по видимым точкам, значки меток — по новой ширине. */
export function zoomPatch(p: ChartParams, range: [number, number]): EChartsCoreOption {
	return {
		yAxis: yAxis(niceScale(visibleValues(p.data, range[0], range[1])), p.colors),
		series: [{ markLine: markLine(p, range) }]
	};
}

export type ZoomState = { startValue?: number; endValue?: number; start?: number; end?: number };
/** Событие `datazoom`: выделение и «назад» панели приходят пачкой (`batch`). */
export type ZoomEvent = ZoomState & { batch?: ZoomState[] };

/** Видимый участок по событию `datazoom`: значения оси или проценты окна; весь период — окно. */
export function zoomRange(event: ZoomEvent, [from, to]: [number, number]): [number, number] {
	const zooms = event.batch ?? [event];
	for (const z of zooms) {
		if (typeof z.startValue === 'number' && typeof z.endValue === 'number') {
			return [Math.max(from, fromChart(z.startValue)), Math.min(to, fromChart(z.endValue))];
		}
		if (typeof z.start === 'number' && typeof z.end === 'number' && (z.start > 0 || z.end < 100)) {
			const span = to - from;
			return [from + (span * z.start) / 100, from + (span * z.end) / 100];
		}
	}
	return [from, to];
}
