import { describe, expect, it } from 'vitest';
import { chartValue, seriesColor } from './totals-chart';

describe('подписи и цвета графика', () => {
	it('деньги со знаком $, минус - U+2212', () => {
		expect(chartValue('money', 21400)).toBe('$21 400');
		expect(chartValue('money', -340)).toBe('−$340');
		expect(chartValue('exp', -5)).toBe('−5');
		expect(chartValue('raw', 0)).toBe('0');
	});

	it('цвета рядов различаются и зависят от темы', () => {
		const colors = Array.from({ length: 50 }, (_, i) => seriesColor(i, false));
		expect(new Set(colors).size).toBe(50);
		expect(seriesColor(3, true)).not.toBe(seriesColor(3, false));
	});
});
