import { describe, expect, it } from 'vitest';
import type { DayOut } from '$lib/api/types';
import { levelForecast, levelForecastText } from './forecast';

const DAY = 86_400_000;
// 2026-10-03 12:00 МСК
const NOW = new Date('2026-10-03T09:00:00Z');

function day(date: string, delta: number | null, opts: { partial?: boolean; covered?: boolean } = {}): DayOut {
	return {
		day: date,
		partial: opts.partial ?? false,
		balance: { exp: { delta, covered: opts.covered ?? delta !== null } }
	} as unknown as DayOut;
}

// Данные аккаунта 1: сегодня (неполные) и пять полных суток, раньше — без покрытия журналом.
const real = [
	day('2026-10-03', 12726, { partial: true }),
	day('2026-10-02', 15931),
	day('2026-10-01', 16134),
	day('2026-09-30', 16136),
	day('2026-09-29', 15919),
	day('2026-09-28', 17193),
	day('2026-09-27', null, { covered: false }),
	day('2026-09-26', null, { covered: false })
];

describe('прогноз до следующего уровня', () => {
	it('темп — среднее по полным суткам, срок и дата', () => {
		const f = levelForecast(real, 540_972, NOW)!;
		expect(f.perDay).toBeCloseTo(16262.6, 1);
		expect(f.etaMs / DAY).toBeCloseTo(33.26, 2);
		expect(Math.abs(f.at.getTime() - NOW.getTime() - f.etaMs)).toBeLessThan(1);
	});

	it('сегодняшние неполные сутки и дни без покрытия не в счёт', () => {
		const days = [
			day('2026-10-03', 1, { partial: true }),
			day('2026-10-02', 1000),
			day('2026-10-01', 3000, { covered: false }),
			day('2026-09-30', 2000),
			day('2026-09-29', null)
		];
		expect(levelForecast(days, 3000, NOW)!.perDay).toBe(1500);
	});

	it('берутся 7 самых свежих подходящих суток', () => {
		const days = Array.from({ length: 9 }, (_, i) => day(`2026-09-${String(30 - i).padStart(2, '0')}`, i < 7 ? 1000 : 9000));
		expect(levelForecast(days, 7000, NOW)!.perDay).toBe(1000);
	});

	it('меньше двух подходящих суток — нет прогноза', () => {
		expect(levelForecast([], 1000, NOW)).toBeNull();
		expect(levelForecast([day('2026-10-02', 1000)], 1000, NOW)).toBeNull();
		expect(levelForecast([day('2026-10-03', 1000, { partial: true }), day('2026-10-02', 1000)], 1000, NOW)).toBeNull();
	});

	it('темп не больше нуля — нет прогноза', () => {
		expect(levelForecast([day('2026-10-02', 0), day('2026-10-01', 0)], 1000, NOW)).toBeNull();
		expect(levelForecast([day('2026-10-02', -500), day('2026-10-01', 200)], 1000, NOW)).toBeNull();
	});

	it('опыта до уровня нет или набран — нет прогноза', () => {
		expect(levelForecast(real, 0, NOW)).toBeNull();
		expect(levelForecast(real, -5, NOW)).toBeNull();
		expect(levelForecast(real, null, NOW)).toBeNull();
	});

	it('меньше суток — часы', () => {
		const f = levelForecast(real, 8_000, NOW)!;
		expect(f.etaMs).toBeLessThan(DAY);
		expect(levelForecastText(f)).toBe('≈ 12 ч (к 03.10) при 16.3K/сут');
	});
});

describe('текст прогноза', () => {
	it('сутки и больше — «≈ N дн.», дата по Москве, темп компактно', () => {
		const f = levelForecast(real, 540_972, NOW)!;
		expect(levelForecastText(f)).toBe('≈ 33 дн. (к 05.11) при 16.3K/сут');
	});

	it('минимум — 1 день и 1 час', () => {
		const days = [day('2026-10-02', 1000), day('2026-10-01', 1000)];
		expect(levelForecastText(levelForecast(days, 1000, NOW)!)).toBe('≈ 1 дн. (к 04.10) при 1\u00a0000/сут');
		expect(levelForecastText(levelForecast(days, 1, NOW)!)).toBe('≈ 1 ч (к 03.10) при 1\u00a0000/сут');
	});

	it('дата — по Москве, не по UTC', () => {
		// 22:00 UTC 03.10 уже 04.10 в Москве
		const f = levelForecast(real, 100, new Date('2026-10-03T22:00:00Z'))!;
		expect(levelForecastText(f)).toContain('(к 04.10)');
	});
});
