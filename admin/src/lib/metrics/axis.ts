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
