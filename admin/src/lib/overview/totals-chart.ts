import { dayShort } from '$lib/daily/text';
import { axisNumbers } from '$lib/metrics/axis';
import type { Palette } from '$lib/metrics/chart';
import type { EChartsCoreOption } from '$lib/metrics/echarts';
import { fmtNum } from '$lib/util/format';
import type { ChartData, MetricKey } from './totals';

/** Штриховка неполных суток (сегодня, до запуска журнала): число не окончательное. */
const HATCH = { symbol: 'rect', symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [2, 4], rotation: Math.PI / 4, color: 'rgba(0, 0, 0, 0.4)' };

const escape = (s: string) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!);

/** Цвет ряда: оттенки по золотому углу, чтобы соседние аккаунты не сливались при любом их числе. */
export function seriesColor(index: number, light: boolean): string {
	return `hsl(${Math.round((index * 137.508) % 360)} 60% ${light ? 42 : 62}%)`;
}

/** 💵 со знаком $, остальное числом; минус - U+2212. */
export function chartValue(metric: MetricKey, v: number): string {
	const body = metric === 'money' ? `$${fmtNum(Math.abs(v))}` : fmtNum(Math.abs(v));
	return v < 0 ? `−${body}` : body;
}

/** Столбцы по суткам, ряд на аккаунт: неполные сутки штрихуются, пропуски остаются пустыми. */
export function barOption(d: ChartData, metric: MetricKey, c: Palette, light = false): EChartsCoreOption {
	return {
		animation: false,
		legend: { type: 'scroll', top: 0, textStyle: { color: c.muted }, pageTextStyle: { color: c.muted }, pageIconColor: c.muted, pageIconInactiveColor: c.faint },
		grid: { left: 8, right: 8, top: 36, bottom: 4, containLabel: true },
		tooltip: {
			trigger: 'axis',
			confine: true,
			transitionDuration: 0,
			backgroundColor: c.surface,
			borderColor: c.line,
			borderWidth: 1,
			padding: [6, 8],
			textStyle: { color: c.text, fontSize: 12 },
			axisPointer: { type: 'shadow' },
			formatter: (params: { axisValue: string; seriesName: string; value: number | { value: number } | null }[]) => {
				const rows = params
					.map((p) => ({ name: p.seriesName, value: p.value !== null && typeof p.value === 'object' ? p.value.value : p.value }))
					.filter((p): p is { name: string; value: number } => typeof p.value === 'number')
					.map((p) => `<div>${escape(p.name)}: <b>${chartValue(metric, p.value)}</b></div>`);
				return [`<div style="opacity:.7">${params[0]?.axisValue ?? ''}</div>`, ...rows].join('');
			}
		},
		xAxis: {
			type: 'category',
			data: d.days.map(dayShort),
			axisLine: { lineStyle: { color: c.line } },
			axisTick: { show: false },
			axisLabel: { color: c.muted, hideOverlap: true }
		},
		yAxis: {
			type: 'value',
			axisLabel: { color: c.muted, formatter: (v: number) => axisNumbers([v])[0]! },
			splitLine: { lineStyle: { color: c.grid } }
		},
		series: d.series.map((s, i) => ({
			type: 'bar',
			name: s.name,
			itemStyle: { color: seriesColor(i, light) },
			emphasis: { focus: 'series' },
			data: s.data.map((v, j) => (v === null ? null : s.partial[j] ? { value: v, itemStyle: { decal: HATCH } } : { value: v }))
		}))
	};
}
