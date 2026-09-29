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
			'💵деньги +$240',
			'💡опыт +4 812',
			'📚знания +86',
			'⚙️детали −120',
			'🔩сырьё +64',
			'🏆личная слава +90'
		]);
		expect(within(items[0]!).getByText('💵')).toHaveAttribute('aria-hidden', 'true');
		expect(within(items[0]!).getByText('деньги')).toHaveClass('sr-only');
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
		expect(within(card).getByRole('list', { name: 'Изменение за день' })).toHaveTextContent('💡опыт нет данных');
		expect(card).toHaveTextContent('Уровень 70 → 71');
		expect(card).toHaveTextContent('Предметов для крафта не было');
		expect(within(card).queryByRole('list', { name: 'Разовое' })).toBeNull();
	});

	it('ошибка обновления — данные помечены устаревшими, время последней загрузки', () => {
		render(DailyCard, {
			day: today,
			ledgerSince: daily.ledger_since,
			error: { kind: 'network', status: 0, message: 'offline' },
			now: NOW,
			loadedAt: new Date('2026-09-28T11:31:00Z')
		});
		const card = screen.getByRole('region', { name: /Итоги дня/ });
		expect(card).toHaveTextContent('Итоги дня · 28.09 (до 14:31)');
		expect(within(card).getByRole('status')).toHaveTextContent('устарело · данные на 14:31 · не обновилось');
	});

	it('после полуночи МСК вчерашний ответ — не «сегодня»', () => {
		// 29.09 00:05 MSK, последний ответ — от 28.09 23:59.
		render(DailyCard, {
			day: today,
			ledgerSince: daily.ledger_since,
			error: null,
			now: new Date('2026-09-28T21:05:00Z'),
			loadedAt: new Date('2026-09-28T20:59:00Z')
		});
		const card = screen.getByRole('region', { name: /Итоги дня/ });
		expect(card).toHaveTextContent('Итоги дня · 29.09');
		expect(card).not.toHaveTextContent('28.09 (до');
		expect(within(card).queryByRole('list', { name: 'Изменение за день' })).toBeNull();
		expect(within(card).getByRole('status')).toHaveTextContent('Итоги за 29.09 ещё не загружены · данные на 28.09 23:59');
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
		// Заголовок, среднее, 8 дней с данными, 22 дня до запуска бота одной строкой и раскрытый разбор
		// сегодня.
		expect(rows).toHaveLength(1 + 1 + 8 + 1 + 1);
		expect(rows.at(-1)).toHaveTextContent('30.08–20.09 — нет данных');
		const avg = rows[1]!;
		expect(avg).toHaveTextContent('среднее за 7 дней');
		// Значки — для глаз, читалке экрана — название (не только title).
		const header = within(rows[0]!).getAllByRole('columnheader');
		expect(header).toHaveLength(10);
		for (const name of ['День', 'деньги', 'опыт', 'знания', 'детали', 'сырьё', 'личная слава', 'Предметы', 'Разовое', 'Потери']) {
			expect(within(rows[0]!).getByRole('columnheader', { name })).toBeInTheDocument();
		}
		expect(within(rows[0]!).getByText('💵')).toHaveAttribute('aria-hidden', 'true');
		const todayRow = rows[2]!;
		expect(todayRow).toHaveTextContent('Сегодня, 28.09');
		expect(todayRow).toHaveTextContent('(до 14:40)');
		expect(todayRow).toHaveTextContent('$218');
		const yesterday = rows[4]!;
		expect(yesterday).toHaveTextContent('27.09 вс');
		expect(yesterday).toHaveTextContent('+2 105');
		const beforeLedger = within(table).getByRole('row', { name: /21\.09 пн/ });
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
		expect(cards).toHaveLength(8 + 1);
		expect(cards.at(-1)).toHaveTextContent('30.08–20.09 — нет данных');
		expect(screen.getByText(/💵 деньги · 💡 опыт/)).toBeInTheDocument();
		expect(cards[0]).toHaveTextContent('Сегодня, 28.09');
		expect(cards[0]).toHaveTextContent('до 14:40');
		expect(cards[0]).toHaveTextContent(`разовое: ${today.income.reduce((a, k) => a + k.count, 0)} · потери: $218`);
		await user.click(within(cards[1]!).getByRole('button', { name: /27\.09/ }));
		expect(within(cards[1]!).getByRole('region', { name: 'Разбор дня 27.09' })).toBeInTheDocument();
	});

	it('после полуночи МСК «сегодня» — по текущему времени, а не по старому ответу', () => {
		// 29.09 00:05 MSK, последний ответ — от 28.09 23:59, новый ещё не пришёл.
		render(DailyView, {
			data: daily,
			error: null,
			now: new Date('2026-09-28T21:05:00Z'),
			loadedAt: new Date('2026-09-28T20:59:00Z')
		});
		const table = screen.getByRole('table', { name: 'Итоги по дням' });
		expect(table).not.toHaveTextContent('Сегодня');
		expect(within(table).getByRole('button', { name: /28\.09 пн \(до 23:59\)/ })).toBeInTheDocument();
		expect(screen.getAllByRole('status')[0]).toHaveTextContent('Итоги за 29.09 ещё не загружены · данные на 28.09 23:59');
		const cards = [...screen.getByRole('list', { name: 'Дни' }).querySelectorAll<HTMLElement>(':scope > li')];
		expect(cards[0]).not.toHaveTextContent('Сегодня');
	});

	it('ошибка обновления — данные помечены устаревшими, время последней загрузки', () => {
		render(DailyView, {
			data: daily,
			error: { kind: 'network', status: 0, message: 'offline' },
			now: NOW,
			loadedAt: new Date('2026-09-28T11:31:00Z')
		});
		expect(screen.getAllByRole('status')[0]).toHaveTextContent('устарело · данные на 14:31 · не обновилось');
		const todayRow = within(screen.getByRole('table', { name: 'Итоги по дням' })).getAllByRole('row')[2]!;
		expect(todayRow).toHaveTextContent('Сегодня, 28.09');
		expect(todayRow).toHaveTextContent('(до 14:31)');
	});

	it('загрузка и ошибка', () => {
		const { unmount } = render(DailyView, { data: null, error: null, now: NOW });
		expect(screen.getByText(/Загрузка/)).toBeInTheDocument();
		unmount();
		render(DailyView, { data: null, error: { kind: 'network', status: 0, message: 'offline' }, now: NOW });
		expect(screen.getByRole('alert')).toHaveTextContent('Итоги недоступны');
	});
});
