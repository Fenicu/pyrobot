import { describe, expect, it } from 'vitest';
import {
	chartOption,
	fmtWhen,
	markersInView,
	niceScale,
	toChart,
	tooltipHtml,
	valueAt,
	visibleValues,
	zoomPatch,
	zoomRange,
	type ChartParams,
	type Series
} from './chart';
import type { Marker } from './events';

// Ступенчатый ряд: 10 с t=100, 20 с t=200, 5 с t=300.
const data: Series = [
	[100, 200, 300],
	[10, 20, 5]
];
const colors = { accent: '#4f8cff', text: '#fff', muted: '#aaa', faint: '#777', grid: '#333', surface: '#111', line: '#444' };
const mark = (x: number, icon = '📈', label = 'слив налички в акции'): Marker => ({ x, icon, label });

describe('ряд под курсором', () => {
	it('значение — последняя точка не позже момента, до первой — нет', () => {
		expect([50, 100, 150, 299, 300, 1000].map((t) => valueAt(data, t))).toEqual([null, 10, 10, 20, 5, 5]);
	});

	it('видимые значения участка: действующее в начале и изменения внутри', () => {
		expect(visibleValues(data, 150, 250)).toEqual([10, 20]);
		expect(visibleValues(data, 350, 400)).toEqual([5]);
	});
});

describe('деления оси значений', () => {
	it('опыт за участок: шаг 5K, подписи с одинаковым числом знаков', () => {
		expect(niceScale([17_497_688, 17_517_514])).toEqual({
			min: 17_495_000,
			max: 17_520_000,
			interval: 5000,
			labels: ['17.495M', '17.500M', '17.505M', '17.510M', '17.515M', '17.520M']
		});
	});

	it('деньги от нуля, постоянное значение — с запасом, пусто — нет делений', () => {
		expect(niceScale([0, 5450])).toMatchObject({ min: 0, max: 6000, interval: 2000 });
		expect(niceScale([110, 110])).toMatchObject({ min: 108, max: 112 });
		expect(niceScale([])).toBeNull();
	});
});

describe('метки и подсказка', () => {
	it('значок — если не ближе 16 px к предыдущему нарисованному; вне участка — нет', () => {
		// 1000 с на 100 px: 10 с на пиксель, 16 px — 160 с.
		const shown = markersInView([mark(0), mark(100), mark(200), mark(2000)], 0, 1000, 100);
		expect(shown.map((m) => [m.x, m.showIcon])).toEqual([
			[0, true],
			[100, false],
			[200, true]
		]);
	});

	it('время по Москве, точное значение, метки рядом и экранирование', () => {
		const at = Date.UTC(2026, 8, 27, 18, 45) / 1000;
		expect(fmtWhen(at)).toBe('27.09 21:45');
		const series: Series = [[at - 60], [5450]];
		const html = tooltipHtml('💵 <деньги>', series, [mark(at + 30), mark(at + 3600, '🛌', 'сон')], at, 60);
		expect(html).toContain('27.09 21:45');
		expect(html).toContain('💵 &lt;деньги&gt;: <b>5\u00a0450</b>');
		expect(html).toContain('📈 слив налички в акции · 21:45');
		expect(html).not.toContain('сон');
		expect(tooltipHtml('x', series, [], at - 3600, 60)).toContain('<b>—</b>');
	});
});

describe('настройки графика', () => {
	const params = (over: Partial<ChartParams> = {}): ChartParams => ({
		name: '💵 деньги',
		data,
		markers: [mark(150), mark(250)],
		range: [0, 400],
		width: 564,
		colors,
		view: { range: [0, 400] },
		...over
	});

	it('ступенька держит значение до следующей точки, время — МСК, окно — общее', () => {
		const option = chartOption(params()) as Record<string, any>;
		const series = option.series[0];
		expect(series.step).toBe('end');
		expect(series.data).toEqual([
			[toChart(100), 10],
			[toChart(200), 20],
			[toChart(300), 5]
		]);
		expect([option.xAxis.min, option.xAxis.max]).toEqual([toChart(0), toChart(400)]);
		expect(option.useUTC).toBe(true);
		expect(series.markLine.data.map((d: { xAxis: number }) => d.xAxis)).toEqual([toChart(150), toChart(250)]);
		// Точки за краями участка не отбрасываются, заголовки панели — по-русски.
		expect(option.toolbox.feature.dataZoom).toMatchObject({
			filterMode: 'none',
			title: { zoom: 'выделить участок', back: 'назад' }
		});
		expect(option.yAxis.axisLabel.formatter(10)).toBe('10');
	});

	it('подсказка: свой курсор или момент ведущего графика', () => {
		const own = chartOption(params()) as Record<string, any>;
		expect(own.tooltip.formatter([{ axisValue: toChart(250) }])).toContain('<b>20</b>');
		const led = chartOption(params({ followed: () => toChart(350) })) as Record<string, any>;
		expect(led.tooltip.formatter([{ axisValue: toChart(250) }])).toContain('<b>5</b>');
	});

	it('выделение: ось значений — по видимому, метки — по участку', () => {
		const patch = zoomPatch(params(), [180, 260]) as Record<string, any>;
		expect([patch.yAxis.min, patch.yAxis.max]).toEqual([10, 20]);
		expect(patch.series[0].markLine.data.map((d: { xAxis: number }) => d.xAxis)).toEqual([toChart(250)]);
	});

	it('участок из события: значения оси, проценты окна, весь период', () => {
		const win: [number, number] = [0, 1000];
		expect(zoomRange({ batch: [{ startValue: toChart(100), endValue: toChart(300) }] }, win)).toEqual([100, 300]);
		expect(zoomRange({ startValue: toChart(-50), endValue: toChart(2000) }, win)).toEqual([0, 1000]);
		expect(zoomRange({ batch: [{ start: 10, end: 60 }] }, win)).toEqual([100, 600]);
		expect(zoomRange({ batch: [{ start: 0, end: 100 }] }, win)).toEqual([0, 1000]);
	});
});
