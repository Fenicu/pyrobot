import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { DailyOut, DayOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import DailyCard from './DailyCard.svelte';
import DailyView from './DailyView.svelte';

const daily = fixture<DailyOut>('daily');
const today = daily.days[0] as DayOut;
// 28.09 14:40 MSK — «сейчас» фикстуры.
const NOW = new Date('2026-09-28T11:40:00Z');

describe('карточка «Итоги дня» на главной', () => {
	it('изменение за день, предметы, разовое, потери — на фикстуре ответа /daily', () => {
		render(DailyCard, { day: today, ledgerSince: daily.ledger_since, error: null, now: NOW });
		const card = screen.getByRole('region', { name: /Итоги дня/ });
		expect(card).toHaveTextContent('Итоги дня · 28.09 (до 14:40)');
		const balance = within(card).getByRole('list', { name: 'Изменение за день' });
		const items = within(balance).getAllByRole('listitem');
		expect(items.map((i) => i.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'💵 +$240',
			'💡 +4 812',
			'📚 +86',
			'⚙️ −120',
			'🔩 +64',
			'🏆 +90'
		]);
		expect(items[3]!.querySelector('.text-bad-fg')).not.toBeNull();
		const craft = within(card).getByRole('list', { name: 'Предметы для крафта' });
		expect(craft).toHaveTextContent('Пуговица ×5');
		const once = within(card).getByRole('list', { name: 'Разовое' });
		expect(once).toHaveTextContent('📒 книги ×3 · +1 371 💡');
		expect(once).toHaveTextContent('👫 задание · +$60, +90 🏆, +722 💡');
		const losses = within(card).getByRole('list', { name: 'Потери и траты' });
		expect(losses).toHaveTextContent('🥷 ограбление −$38, +118 💡');
		expect(losses).toHaveTextContent('⛏ начало дел ×6 −$180');
		expect(card.querySelector('a[href="/daily"]')).not.toBeNull();
	});

	it('«нет данных» вместо null, уровень, пустые разделы', () => {
		const day: DayOut = {
			...today,
			balance: { ...today.balance, exp: { delta: null, covered: false } },
			level: { from: 70, to: 71 },
			items: {},
			income: [],
			losses: []
		};
		render(DailyCard, { day, ledgerSince: daily.ledger_since, error: null, now: NOW });
		const card = screen.getByRole('region', { name: /Итоги дня/ });
		expect(within(card).getByRole('list', { name: 'Изменение за день' })).toHaveTextContent('💡 нет данных');
		expect(card).toHaveTextContent('Уровень 70 → 71');
		expect(card).toHaveTextContent('Предметов для крафта не было');
		expect(within(card).queryByRole('list', { name: 'Разовое' })).toBeNull();
	});

	it('загрузка и ошибка', () => {
		const { unmount } = render(DailyCard, { day: null, ledgerSince: null, error: null, now: NOW });
		expect(screen.getByRole('region', { name: /Итоги дня/ })).toHaveTextContent('Загрузка');
		unmount();
		render(DailyCard, {
			day: null,
			ledgerSince: null,
			error: { kind: 'network', status: 0, message: 'offline' },
			now: NOW
		});
		expect(screen.getByRole('alert')).toHaveTextContent('Итоги недоступны');
	});
});

describe('страница «Итоги»', () => {
	it('ПК: таблица за 30 дней, среднее за 7 дней, день до журнала — без разового', () => {
		render(DailyView, { data: daily, error: null, now: NOW });
		const table = screen.getByRole('table', { name: 'Итоги по дням' });
		const rows = within(table).getAllByRole('row');
		// Заголовок, среднее, 30 дней и раскрытый разбор сегодня.
		expect(rows).toHaveLength(1 + 1 + 30 + 1);
		const avg = rows[1]!;
		expect(avg).toHaveTextContent('среднее за 7 дней');
		const header = within(rows[0]!).getAllByRole('columnheader').map((c) => c.textContent?.trim());
		expect(header).toEqual(['День', '💵', '💡', '📚', '⚙️', '🔩', '🏆', 'Предметы', 'Разовое', 'Потери']);
		const todayRow = rows[2]!;
		expect(todayRow).toHaveTextContent('Сегодня, 28.09');
		expect(todayRow).toHaveTextContent('(до 14:40)');
		expect(todayRow).toHaveTextContent('$218');
		const yesterday = rows[4]!;
		expect(yesterday).toHaveTextContent('27.09 вс');
		expect(yesterday).toHaveTextContent('+2 105');
		const beforeLedger = within(table).getByRole('row', { name: /20\.09/ });
		expect(within(beforeLedger).getAllByTitle(/журнал прихода с 22\.09/)).toHaveLength(3);
		expect(beforeLedger).not.toHaveTextContent('неполный');
		// День запуска журнала — неполный; ноль — без цвета прихода.
		expect(within(table).getByRole('row', { name: /22\.09/ })).toHaveTextContent('22.09 вт (неполный)');
		const zero = within(within(table).getByRole('row', { name: /27\.09/ })).getByText('0');
		expect(zero).toHaveClass('text-fg-muted');
	});

	it('клик по дню раскрывает его разбор', async () => {
		const user = userEvent.setup();
		render(DailyView, { data: daily, error: null, now: NOW });
		const table = screen.getByRole('table', { name: 'Итоги по дням' });
		const open = within(table).getByRole('button', { name: /27\.09 вс/ });
		expect(open).toHaveAttribute('aria-expanded', 'false');
		await user.click(open);
		expect(open).toHaveAttribute('aria-expanded', 'true');
		const detail = within(table).getByRole('region', { name: 'Разбор дня 27.09' });
		expect(detail).toHaveTextContent('🚇 метро');
		expect(within(table).queryByRole('region', { name: 'Разбор дня 28.09' })).toBeNull();
	});

	it('телефон: карточки дней с кратким разовым и потерями', async () => {
		const user = userEvent.setup();
		render(DailyView, { data: daily, error: null, now: NOW });
		const list = screen.getByRole('list', { name: 'Дни' });
		const cards = [...list.querySelectorAll<HTMLElement>(':scope > li')];
		expect(cards).toHaveLength(30);
		expect(cards[0]).toHaveTextContent('Сегодня, 28.09');
		expect(cards[0]).toHaveTextContent('до 14:40');
		expect(cards[0]).toHaveTextContent(`разовое: ${today.income.reduce((a, k) => a + k.count, 0)} · потери: $218`);
		await user.click(within(cards[1]!).getByRole('button', { name: /27\.09/ }));
		expect(within(cards[1]!).getByRole('region', { name: 'Разбор дня 27.09' })).toBeInTheDocument();
	});

	it('загрузка и ошибка', () => {
		const { unmount } = render(DailyView, { data: null, error: null, now: NOW });
		expect(screen.getByText(/Загрузка/)).toBeInTheDocument();
		unmount();
		render(DailyView, { data: null, error: { kind: 'network', status: 0, message: 'offline' }, now: NOW });
		expect(screen.getByRole('alert')).toHaveTextContent('Итоги недоступны');
	});
});
