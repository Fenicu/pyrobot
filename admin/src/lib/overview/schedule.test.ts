import { describe, expect, it } from 'vitest';
import type { ScheduleItem, ScheduleOut, SharedScheduleItem } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import {
	hourTicks,
	itemText,
	position,
	segments,
	sharedBands,
	sharedMarks
} from './schedule';

const sched = fixture<ScheduleOut>('overview_schedule');

describe('расчёт позиций (position)', () => {
	const now = new Date('2026-10-17T09:00:00Z'); // 12:00 MSK
	const hours = 12;

	it('граница: отметка точно в now — pos 0, ready', () => {
		const res = position(now, now, hours);
		expect(res.pos).toBe(0);
		expect(res.ready).toBe(true);
	});

	it('граница: отметка в прошлом — pos 0, ready true (прижата влево)', () => {
		const past = new Date('2026-10-17T08:30:00Z');
		const res = position(past, now, hours);
		expect(res.pos).toBe(0);
		expect(res.ready).toBe(true);
	});

	it('граница: отметка точно на until — pos 1, ready false', () => {
		const until = new Date('2026-10-17T21:00:00Z');
		const res = position(until, now, hours);
		expect(res.pos).toBe(1);
		expect(res.ready).toBe(false);
	});

	it('середина окна: 6 часов вперёд — pos 0.5, ready false', () => {
		const mid = new Date('2026-10-17T15:00:00Z');
		const res = position(mid, now, hours);
		expect(res.pos).toBeCloseTo(0.5, 4);
		expect(res.ready).toBe(false);
	});

	it('за пределами окна справа — прижимается к 1', () => {
		const future = new Date('2026-10-18T00:00:00Z');
		const res = position(future, now, hours);
		expect(res.pos).toBe(1);
		expect(res.ready).toBe(false);
	});
});

describe('отрезки занятости (segments)', () => {
	const now = new Date('2026-10-17T09:00:00Z'); // 12:00 MSK
	const hours = 12; // до 21:00 UTC

	it('отрезок целиком внутри окна', () => {
		const items: ScheduleItem[] = [
			{
				kind: 'busy',
				at: '2026-10-17T12:00:00Z',
				until: '2026-10-17T15:00:00Z',
				source: 'state',
				detail: 'harvest'
			}
		];
		const res = segments(items, now, hours);
		expect(res).toHaveLength(1);
		expect(res[0]!.kind).toBe('busy');
		expect(res[0]!.detail).toBe('harvest');
		expect(res[0]!.start).toBeCloseTo(3 / 12, 4);
		expect(res[0]!.end).toBeCloseTo(6 / 12, 4);
	});

	it('обрезка отрезка, начавшегося в прошлом и заканчивающегося в окне', () => {
		const items: ScheduleItem[] = [
			{
				kind: 'busy',
				at: '2026-10-17T08:00:00Z',
				until: '2026-10-17T11:00:00Z',
				source: 'state',
				detail: 'job'
			}
		];
		const res = segments(items, now, hours);
		expect(res).toHaveLength(1);
		expect(res[0]!.start).toBe(0);
		expect(res[0]!.end).toBeCloseTo(2 / 12, 4);
	});

	it('обрезка отрезка, уходящего за правый край окна', () => {
		const items: ScheduleItem[] = [
			{
				kind: 'sleep',
				at: '2026-10-17T18:00:00Z',
				until: '2026-10-18T02:00:00Z',
				source: 'state',
				detail: 'sleep_bridge'
			}
		];
		const res = segments(items, now, hours);
		expect(res).toHaveLength(1);
		expect(res[0]!.kind).toBe('sleep');
		expect(res[0]!.start).toBeCloseTo(9 / 12, 4);
		expect(res[0]!.end).toBe(1);
	});

	it('отрезок, полностью завершившийся в прошлом, отсекается', () => {
		const items: ScheduleItem[] = [
			{
				kind: 'busy',
				at: '2026-10-17T06:00:00Z',
				until: '2026-10-17T08:00:00Z',
				source: 'state',
				detail: 'harvest'
			}
		];
		const res = segments(items, now, hours);
		expect(res).toHaveLength(0);
	});

	it('отметки без until игнорируются', () => {
		const items: ScheduleItem[] = [
			{
				kind: 'book_ready',
				at: '2026-10-17T10:00:00Z',
				until: null,
				source: 'state',
				detail: null
			}
		];
		const res = segments(items, now, hours);
		expect(res).toHaveLength(0);
	});
});

describe('засечки часов (hourTicks)', () => {
	it('число засечек и метки МСК для 12 часов от 12:00 МСК', () => {
		const now = new Date('2026-10-17T09:00:00Z'); // 12:00 MSK
		const ticks = hourTicks(now, 12);
		expect(ticks.length).toBeGreaterThanOrEqual(12);
		expect(ticks[0]!.label).toBe('12:00');
		expect(ticks[0]!.pos).toBe(0);
		expect(ticks[ticks.length - 1]!.label).toBe('00:00');
		expect(ticks[ticks.length - 1]!.pos).toBe(1);
		for (let i = 1; i < ticks.length; i++) {
			expect(ticks[i]!.pos).toBeGreaterThan(ticks[i - 1]!.pos);
		}
	});

	it('засечки при старте не в круглый час (12:20 МСК)', () => {
		const now = new Date('2026-10-17T09:20:00Z'); // 12:20 MSK
		const ticks = hourTicks(now, 12);
		expect(ticks[0]!.label).toBe('13:00');
		expect(ticks[0]!.pos).toBeCloseTo(40 / 720, 3);
	});
});

describe('общие полосы (sharedBands)', () => {
	const now = new Date('2026-10-17T11:00:00Z'); // 14:00 MSK
	const hours = 12; // до 02:00 MSK 18 октября

	it('полоса ночи корректно пересекает полночь МСК', () => {
		const shared: SharedScheduleItem[] = [
			{
				kind: 'night',
				at: '2026-10-17T19:00:00Z', // 22:00 MSK
				until: '2026-10-18T05:00:00Z', // 08:00 MSK
				label: '22:00–08:00'
			}
		];
		const bands = sharedBands(shared, now, hours);
		expect(bands).toHaveLength(1);
		const [night] = bands;
		expect(night!.kind).toBe('night');
		expect(night!.label).toBe('22:00–08:00');
		// От 14:00 до 22:00 = 8 часов -> start = 8 / 12 = 0.6667
		expect(night!.start).toBeCloseTo(8 / 12, 4);
		// Конец окна в 02:00 МСК -> обрезано до конца шкалы (1.0)
		expect(night!.end).toBe(1);
	});

	it('фабрика и лотерея попадают в полосы', () => {
		const bands = sharedBands(sched.shared, now, hours);
		const kinds = bands.map((b) => b.kind);
		expect(kinds).toContain('factory');
		expect(kinds).toContain('lottery');
		expect(kinds).toContain('night');
	});

	it('битва без until не попадает в bands, но есть в sharedMarks', () => {
		const bands = sharedBands(sched.shared, now, hours);
		expect(bands.some((b) => b.kind === 'battle')).toBe(false);

		const marks = sharedMarks(sched.shared, new Date('2026-10-17T09:00:00Z'), 12);
		expect(marks.some((m) => m.kind === 'battle')).toBe(true);
	});
});

describe('подписи и иконки элементов (itemText)', () => {
	it('возвращает правильные иконки и текст для состояния и планировщика', () => {
		expect(itemText({ kind: 'busy', at: '', until: '', source: 'state', detail: 'harvest' })).toEqual({
			icon: '⏳',
			text: 'Занят: добыча'
		});
		expect(itemText({ kind: 'sleep', at: '', until: '', source: 'state', detail: 'sleep_bridge' })).toEqual({
			icon: '🛌',
			text: 'Сон (под мостом)'
		});
		expect(itemText({ kind: 'fastfood_ready', at: '', until: null, source: 'state', detail: null })).toEqual({
			icon: '🍔',
			text: 'Фастфуд'
		});
		expect(itemText({ kind: 'motivation', at: '', until: null, source: 'planner', detail: null })).toEqual({
			icon: '🔥',
			text: '+1 мотивация'
		});
		expect(itemText({ kind: 'artifact_end', at: '', until: null, source: 'state', detail: 'Катана' })).toEqual({
			icon: '👾',
			text: 'Артефакт: Катана'
		});
		expect(itemText({ kind: 'artifact_end', at: '', until: null, source: 'state', detail: 'light' })).toEqual({
			icon: '👾',
			text: 'Артефакт: 🔦 Фонарь Sw-ет'
		});
	});
});
