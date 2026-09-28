import { describe, expect, it } from 'vitest';
import type { DailyOut, DayOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import {
	BALANCE,
	KIND,
	amountsText,
	average,
	dayLabel,
	incomeCount,
	incomeText,
	itemsCount,
	kindText,
	lossesMoney,
	lossText,
	moneySigned,
	signed
} from './text';

const daily = fixture<DailyOut>('daily');
const [today, yesterday] = daily.days as [DayOut, DayOut];

describe('словари итогов дня', () => {
	it('виды журнала прихода — по-русски со значком; незнакомый — как есть', () => {
		expect(kindText('book')).toBe('📒 книги');
		expect(kindText('robbery')).toBe('🥷 ограбление');
		expect(kindText('new_kind')).toBe('new_kind');
		for (const day of daily.days) {
			for (const k of [...day.income, ...day.losses]) expect(k.kind in KIND, k.kind).toBe(true);
		}
	});

	it('баланс — все ключи ответа, 🏆 — личная слава', () => {
		expect(BALANCE.map((b) => b.key)).toEqual(Object.keys(today.balance));
		expect(BALANCE.find((b) => b.key === 'glory')?.title).toBe('личная слава');
	});

	it('знак: плюс, минус (−), ноль; деньги — с $', () => {
		expect(signed(4812)).toBe('+4 812');
		expect(signed(-120)).toBe('−120');
		expect(signed(0)).toBe('0');
		expect(moneySigned(1240)).toBe('+$1 240');
		expect(moneySigned(-340)).toBe('−$340');
	});

	it('разовое и потери компактно', () => {
		expect(incomeText({ kind: 'book', count: 4, amounts: { exp: 720 } })).toBe('📒 книги ×4 · +720 💡');
		expect(incomeText({ kind: 'prizebox', count: 1, amounts: { money: 120 } })).toBe('🎁 коробка · +$120');
		expect(incomeText({ kind: 'task', count: 1, amounts: { trophies: 90, knowledge: 9 } })).toBe(
			'👫 задание · +90 🏆, +9 📚'
		);
		expect(lossText({ kind: 'robbery', count: 1, amounts: { money: -340, exp: 118 } })).toBe(
			'🥷 ограбление −$340, +118 💡'
		);
		expect(lossText({ kind: 'deed_start', count: 6, amounts: { money: -180 } })).toBe('⛏ начало дел ×6 −$180');
		expect(amountsText({ upgrades_white: 2, containers_small: 1 })).toBe('+2 ⚪, +1 🗳М');
	});

	it('счётчики дня: предметы, события разового, деньги потерь', () => {
		expect(itemsCount(today)).toBe(Object.values(today.items).reduce((a, b) => a + b, 0));
		expect(incomeCount(today)).toBe(today.income.reduce((a, k) => a + k.count, 0));
		expect(lossesMoney(today)).toBe(38 + 180);
	});

	it('подпись дня: сегодня, день недели', () => {
		expect(dayLabel(today.day, today.day)).toBe('Сегодня, 28.09');
		expect(dayLabel(yesterday.day, today.day)).toBe('27.09 вс');
	});

	it('среднее за 7 дней — по полным дням с данными, без сегодня и без null', () => {
		const avg = average(daily.days);
		const full = daily.days.slice(1, 8).filter((d) => !d.partial);
		expect(full).toHaveLength(5);
		const money = full.map((d) => d.balance.money!.delta!);
		expect(avg.balance.money).toBe(Math.round(money.reduce((a, b) => a + b, 0) / money.length));
		// Сырьё без данных в двух из пяти дней — среднее по трём.
		const raw = full.map((d) => d.balance.raw!.delta).filter((v): v is number => v !== null);
		expect(raw).toHaveLength(3);
		expect(avg.balance.raw).toBe(Math.round(raw.reduce((a, b) => a + b, 0) / 3));
		expect(avg.items).toBe(Math.round(full.reduce((a, d) => a + itemsCount(d), 0) / 5));
		expect(average([today]).balance.money).toBeNull();
	});
});
