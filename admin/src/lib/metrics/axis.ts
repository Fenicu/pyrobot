import type uPlot from 'uplot';

const NL = '\n';

/** Подписи оси времени по-русски: 24 часа, дд.мм; дата — под первым делением и при смене дня.
 * Таблица uPlot: шаг делений, формат, форматы смены года/месяца/дня/часа/минуты/секунды, режим. */
export const TIME_VALUES: uPlot.Axis.Values = [
	[3600 * 24 * 365, '{YYYY}', null, null, null, null, null, null, 1],
	[3600 * 24 * 28, '{MM}.{YYYY}', null, null, null, null, null, null, 1],
	[3600 * 24, '{DD}.{MM}', NL + '{YYYY}', null, null, null, null, null, 1],
	[3600, '{HH}:{mm}', NL + '{DD}.{MM}.{YYYY}', null, NL + '{DD}.{MM}', null, null, null, 1],
	[60, '{HH}:{mm}', NL + '{DD}.{MM}.{YYYY}', null, NL + '{DD}.{MM}', null, null, null, 1],
	[1, '{HH}:{mm}:{ss}', NL + '{DD}.{MM}.{YYYY}', null, NL + '{DD}.{MM}', null, null, null, 1]
];

/** Подписи оси значений в стиле главной (17.52M, 520K, 5 000) с наименьшим числом знаков, при
 * котором каждое деление записано точно: 17.505M, а не округлённое «17.50M» рядом с 17.51M. */
export function axisNumbers(splits: number[]): string[] {
	const max = Math.max(0, ...splits.map((v) => Math.abs(v)));
	const [unit, suffix] = max >= 1e6 ? [1e6, 'M'] : max >= 1e4 ? [1e3, 'K'] : [1, ''];
	const exact = (d: number) =>
		splits.every((v) => {
			const scaled = (v / unit) * 10 ** d;
			return Math.abs(scaled - Math.round(scaled)) < 1e-6;
		});
	let digits = 0;
	while (digits < 4 && !exact(digits)) digits++;
	return splits.map((v) =>
		unit === 1 ? fixed(v, digits) : `${(v / unit).toFixed(digits)}${suffix}`
	);
}

function fixed(value: number, digits: number): string {
	return new Intl.NumberFormat('ru-RU', {
		minimumFractionDigits: digits,
		maximumFractionDigits: digits
	}).format(value);
}

export const AXIS_FONT = '12px system-ui, sans-serif';
let measurer: CanvasRenderingContext2D | null | undefined;

/** Ширина оси значений по самой длинной подписи: фиксированная обрезала 17 520 000 слева. */
export function axisSize(values: string[] | null | undefined): number {
	measurer ??= typeof document === 'undefined' ? null : document.createElement('canvas').getContext('2d');
	const width = (text: string) => {
		if (!measurer) return text.length * 7;
		measurer.font = AXIS_FONT;
		return measurer.measureText(text).width;
	};
	const longest = Math.max(0, ...(values ?? []).map(width));
	// Засечки (10) и зазор до подписи (5) — как у uPlot по умолчанию, плюс запас.
	return Math.ceil(longest) + 20;
}
