import { describe, expect, it } from 'vitest';
import { axisNumbers, axisSize, TIME_VALUES } from './axis';

describe('Подписи оси значений', () => {
	it('опыт за день: миллионы с точностью, при которой деления различаются', () => {
		expect(axisNumbers([17_500_000, 17_510_000, 17_520_000])).toEqual(['17.50M', '17.51M', '17.52M']);
		expect(axisNumbers([17_000_000, 18_000_000])).toEqual(['17M', '18M']);
		expect(axisNumbers([17_505_000, 17_510_000])).toEqual(['17.505M', '17.510M']);
	});

	it('тысячи — K, до 10 000 — числом с разрядами', () => {
		expect(axisNumbers([500_000, 520_000])).toEqual(['500K', '520K']);
		expect(axisNumbers([0, 2500, 5000])).toEqual(['0', '2 500', '5 000']);
		expect(axisNumbers([0, 50, 100])).toEqual(['0', '50', '100']);
		expect(axisNumbers([1.5, 1.6, 1.7])).toEqual(['1,5', '1,6', '1,7']);
	});

	it('ширина оси растёт с длиной подписи', () => {
		expect(axisSize(['17.52M'])).toBeGreaterThan(axisSize(['0']));
		expect(axisSize(null)).toBe(20);
	});
});

describe('Подписи оси времени', () => {
	it('24 часа и дд.мм, без am/pm и м/д/г', () => {
		const text = JSON.stringify(TIME_VALUES);
		expect(text).toContain('{HH}:{mm}');
		expect(text).toContain('{DD}.{MM}');
		expect(text).not.toMatch(/\{h\}|\{aa\}|\{M\}\/\{D\}/);
	});
});
