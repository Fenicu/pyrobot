import type { ECharts } from './echarts';

/** Графики метрик на странице: курсор с подсказкой и выделенный участок — на всех сразу, по времени.
 * Своя связка, а не `echarts.connect`: та передаёт подсказку номером точки, и у графика с меньшим
 * числом точек (🔋 — 12 за день против 127 у 💵) подсказка пропадала. Ведёт график под мышью —
 * ответные события остальных дальше не расходятся. */
const charts = new Set<ECharts>();
let leader: ECharts | null = null;
/** Момент под курсором ведущего графика (единицы оси времени). */
let pointer: number | null = null;

/** Момент под курсором для подсказки графика-ведомого: переданная подсказка прилипает к ближайшей
 * точке его ряда, а значение нужно ровно в момент курсора. У ведущего — null (свой курсор). */
export function followed(chart: ECharts): number | null {
	return chart === leader ? null : pointer;
}

function others(from: ECharts, act: (c: ECharts) => void) {
	if (from !== leader) return;
	for (const c of charts) if (c !== from && !c.isDisposed()) act(c);
}

type PointerEvent = { axesInfo?: { axisDim?: string; value?: number }[] };

/** Подключить график к связке; возвращает отключение. */
export function link(chart: ECharts): () => void {
	charts.add(chart);
	const zr = chart.getZr();
	const over = () => {
		leader = chart;
	};
	const out = () => {
		others(chart, (c) => {
			c.dispatchAction({ type: 'hideTip' });
			c.dispatchAction({ type: 'updateAxisPointer', currTrigger: 'leave' });
		});
		if (leader === chart) {
			leader = null;
			pointer = null;
		}
	};
	zr.on('mousemove', over);
	zr.on('globalout', out);
	chart.on('updateAxisPointer', (event) => {
		const value = (event as PointerEvent).axesInfo?.find((a) => a.axisDim === 'x')?.value;
		if (typeof value !== 'number') return;
		if (chart === leader) pointer = value;
		others(chart, (c) => {
			const x = c.convertToPixel({ xAxisIndex: 0 }, value);
			if (typeof x === 'number') c.dispatchAction({ type: 'showTip', x, y: c.getHeight() / 2 });
		});
	});
	return () => {
		zr.off('mousemove', over);
		zr.off('globalout', out);
		charts.delete(chart);
		if (leader === chart) leader = null;
	};
}

/** Выделенный участок ведущего графика — остальным (значения оси времени графика). */
export function shareZoom(chart: ECharts, startValue: number, endValue: number): void {
	others(chart, (c) => c.dispatchAction({ type: 'dataZoom', startValue, endValue }));
}
