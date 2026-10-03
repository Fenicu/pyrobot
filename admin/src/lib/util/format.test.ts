import { describe, expect, it } from 'vitest';
import { fmtCompact, fmtDayMonth, fmtMoment, fmtNum, fmtRelative, fmtSpan, fmtTime, mskDayStart } from './format';

const NOW = new Date('2026-09-27T20:27:51Z');

describe('формат', () => {
	it('время по Москве', () => {
		expect(fmtTime('2026-09-28T02:05:09Z')).toBe('05:05');
		expect(fmtMoment('2026-09-27T19:05:08Z', NOW)).toBe('22:05');
		expect(fmtMoment('2026-09-28T02:05:09Z', NOW)).toBe('28.09 05:05');
		expect(fmtTime(null)).toBe('—');
	});

	it('относительное время и длительность', () => {
		expect(fmtRelative('2026-09-27T21:05:51Z', NOW)).toBe('через 38 мин');
		expect(fmtRelative('2026-09-27T20:22:51Z', NOW)).toBe('5 мин назад');
		expect(fmtRelative('2026-09-27T20:28:00Z', NOW)).toBe('сейчас');
		expect(fmtSpan(543.5)).toBe('9 мин');
		expect(fmtSpan(7800)).toBe('2 ч 10 мин');
		expect(fmtSpan(97200)).toBe('1 д 3 ч');
	});

	it('числа', () => {
		expect(fmtNum(21946)).toBe('21 946');
		expect(fmtCompact(17520102)).toBe('17.52M');
		expect(fmtCompact(47)).toBe('47');
	});

	it('день и месяц по Москве', () => {
		expect(fmtDayMonth(new Date('2026-11-05T09:00:00Z'))).toBe('05.11');
		expect(fmtDayMonth(new Date('2026-10-03T22:00:00Z'))).toBe('04.10');
	});

	it('начало суток МСК', () => {
		expect(mskDayStart(new Date('2026-09-27T22:30:00Z')).toISOString()).toBe('2026-09-27T21:00:00.000Z');
		expect(mskDayStart(NOW).toISOString()).toBe('2026-09-26T21:00:00.000Z');
	});
});
