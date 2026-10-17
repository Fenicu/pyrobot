import { describe, expect, it } from 'vitest';
import type { OverviewTotals, TotalsDelta } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import {
	chartSeries,
	formatDelta,
	levelsText,
	parseDays,
	pollIntervalMs,
	TOTALS_POLL_LONG_MS,
	TOTALS_POLL_TODAY_MS,
	windowNote
} from './totals';

const data = fixture<OverviewTotals>('overview_totals');
const delta = (over: Partial<TotalsDelta>): TotalsDelta => ({ delta: 100, covered: true, accounts: 3, ...over });

describe('formatDelta', () => {
	it('деньги со знаком $, остальное со знаком и без него', () => {
		expect(formatDelta(delta({ delta: 91200 }), 'money', 3)).toBe('+$91 200');
		expect(formatDelta(delta({ delta: -840 }), 'money', 3)).toBe('−$840');
		expect(formatDelta(delta({ delta: 73900 }), 'exp', 3)).toBe('+73 900');
		expect(formatDelta(delta({ delta: 0 }), 'raw', 3)).toBe('0');
	});

	it('нет данных: прочерк, а не ноль', () => {
		expect(formatDelta(delta({ delta: null, covered: false, accounts: 0 }), 'money', 3)).toBe('—');
		expect(formatDelta(delta({ delta: null, covered: false, accounts: 0 }), 'glory', 1)).toBe('—');
	});

	it('сумма не по всем аккаунтам: «по 5 из 7»', () => {
		expect(formatDelta(delta({ delta: 700, covered: false, accounts: 5 }), 'details', 7)).toBe('+700 · по 5 из 7');
	});

	it('covered=false при accounts=total (окно длиннее хранения) числа не даёт', () => {
		expect(formatDelta(delta({ delta: null, covered: false, accounts: 0 }), 'exp', 2)).toBe('—');
	});
});

describe('levelsText', () => {
	it('null (окно длиннее хранения) — прочерк; 0 — «0»', () => {
		expect(levelsText(null)).toBe('—');
		expect(levelsText(0)).toBe('0');
		expect(levelsText(3)).toBe('+3');
	});
});

describe('windowNote', () => {
	it('окно внутри хранения — без пометки', () => {
		expect(windowNote(data)).toBeNull();
	});

	it('окно начинается раньше порога хранения: «метрики хранятся N дней»', () => {
		const short = { ...data, first: '2026-09-18', metrics_floor: '2026-10-08T09:00:00+00:00' };
		expect(windowNote(short)).toBe('Метрики хранятся 9 дней: изменение за этот период не посчитать.');
		expect(windowNote({ ...short, metrics_floor: '2026-10-10T21:00:00+00:00', first: '2026-10-11' })).toBeNull();
		expect(windowNote({ ...short, metrics_floor: '2026-10-10T21:00:01+00:00', first: '2026-10-11' })).not.toBeNull();
	});

	it('склонение дней', () => {
		const at = (days: number) => ({
			...data,
			first: '2026-09-18',
			metrics_floor: new Date(Date.parse(data.now) - days * 86_400_000).toISOString()
		});
		expect(windowNote(at(1))).toContain('хранятся 1 день');
		expect(windowNote(at(3))).toContain('хранятся 3 дня');
		expect(windowNote(at(10))).toContain('хранятся 10 дней');
		expect(windowNote(at(21))).toContain('хранятся 21 день');
	});
});

describe('chartSeries', () => {
	const names = (id: number) => `acc${id}`;

	it('дни по возрастанию, ряд на аккаунт, пропуски null на месте', () => {
		const s = chartSeries(data, 'money', names);
		expect(s.days).toEqual(['2026-10-11', '2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16', '2026-10-17']);
		expect(s.series.map((x) => x.name)).toEqual(['acc1', 'acc2', 'acc3']);
		expect(s.series[0]!.data).toEqual([null, null, null, 15900, 18200, 21400, 4812]);
		expect(s.series[2]!.data.every((v) => v === null)).toBe(true);
	});

	it('неполные дни помечены (штрихуются), полные нет', () => {
		const s = chartSeries(data, 'money', names);
		expect(s.series[0]!.partial).toEqual([true, true, true, true, false, false, true]);
	});

	it('показатель выбирает ключ баланса', () => {
		const s = chartSeries(data, 'glory', names);
		expect(s.series[0]!.data).toEqual([null, null, null, null, null, 55, null]);
	});

	it('нет аккаунтов — пустые ряды и дни', () => {
		const empty = { ...data, accounts: [] };
		expect(chartSeries(empty, 'money', names)).toEqual({ days: [], series: [] });
	});

	it('сегодня: одна группа', () => {
		const one = { ...data, days: 1, accounts: data.accounts.map((a) => ({ ...a, days: a.days.slice(0, 1) })) };
		const s = chartSeries(one, 'exp', names);
		expect(s.days).toEqual(['2026-10-17']);
		expect(s.series.map((x) => x.data)).toEqual([[9100], [4000], [null]]);
	});
});

describe('опрос', () => {
	it('«Сегодня» раз в 30 с, 7 и 30 дней раз в 5 минут', () => {
		expect(pollIntervalMs(1)).toBe(TOTALS_POLL_TODAY_MS);
		expect(pollIntervalMs(7)).toBe(TOTALS_POLL_LONG_MS);
		expect(pollIntervalMs(30)).toBe(TOTALS_POLL_LONG_MS);
		expect(TOTALS_POLL_TODAY_MS).toBe(30_000);
		expect(TOTALS_POLL_LONG_MS).toBe(300_000);
	});
});

describe('parseDays', () => {
	it('?days=: только 1, 7, 30; иное — 7', () => {
		expect(parseDays('1')).toBe(1);
		expect(parseDays('7')).toBe(7);
		expect(parseDays('30')).toBe(30);
		expect(parseDays('2')).toBe(7);
		expect(parseDays('x')).toBe(7);
		expect(parseDays(null)).toBe(7);
	});
});
