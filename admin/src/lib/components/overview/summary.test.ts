import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import type { OverviewAccount } from '$lib/api/types';
import { mergeRows, type OverviewRow } from '$lib/overview/table';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import SummaryCards from './SummaryCards.svelte';
import SummaryTable from './SummaryTable.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const extras = fixture<OverviewAccount[]>('overview_accounts');
const NOW = new Date('2026-10-17T09:18:00Z');

const accounts = [
	accountOut(1, 'Альфа', { level: 31, company: 'hooli' }),
	accountOut(2, 'Бета', { level: 12, busy: { activity: 'job', until: '2026-10-17T09:30:00Z' } }),
	accountOut(3, 'Гамма', { level: 4, status: 'deleting' }),
	accountOut(4, 'Дельта')
];
const rows = (): OverviewRow[] => mergeRows(accounts, extras);

afterEach(() => {
	cleanup();
	vi.mocked(goto).mockClear();
});

const rowOf = (name: string) => screen.getByText(name).closest('tr')!;

describe('таблица сводки', () => {
	function renderTable(over: Partial<{ sort: { key: null | 'money'; dir: 'asc' | 'desc' }; onsort: () => void }> = {}) {
		const onsort = over.onsort ?? vi.fn();
		const view = render(SummaryTable, { rows: rows(), now: NOW, sort: over.sort ?? { key: null, dir: 'asc' }, onsort });
		return { onsort, ...view };
	}

	it('строка на аккаунт: имя, числа с разрядами, навыки', () => {
		renderTable();
		const row = within(rowOf('🤖Альфа'));
		expect(row.getByText('18 450')).toBeInTheDocument();
		expect(row.getByText('14/20')).toBeInTheDocument();
		expect(row.getByText('🔨31 🎓28 🐿12 🐢9')).toBeInTheDocument();
		expect(row.getByText('⛏ добыча')).toBeInTheDocument();
	});

	it('аккаунт без снимка: прочерки вместо чисел', () => {
		renderTable();
		const row = within(rowOf('Дельта'));
		expect(row.getAllByText('—').length).toBeGreaterThan(5);
	});

	it('строка «Всего» суммирует деньги, знания, детали и сырьё', () => {
		renderTable();
		const total = screen.getByRole('row', { name: /Всего/ });
		expect(within(total).getByText('24 450')).toBeInTheDocument();
		expect(within(total).getByText('322')).toBeInTheDocument();
		expect(within(total).getByText('45')).toBeInTheDocument();
		expect(within(total).getByText('22')).toBeInTheDocument();
	});

	it('устаревший снимок приглушён и подписан временем', () => {
		renderTable();
		const row = rowOf('Бета');
		expect(row).toHaveAttribute('data-stale', 'true');
		expect(within(row).getByText('снимок от 11:50')).toBeInTheDocument();
		expect(rowOf('🤖Альфа')).not.toHaveAttribute('data-stale', 'true');
	});

	it('цель: расхождение с игрой подсвечено, остальные нет', () => {
		renderTable();
		expect(within(rowOf('Бета')).getByTestId('battle')).toHaveAttribute('data-mismatch', 'true');
		expect(within(rowOf('🤖Альфа')).getByTestId('battle')).toHaveAttribute('data-mismatch', 'false');
		expect(within(rowOf('🤖Альфа')).getByTestId('battle')).toHaveTextContent('🤖Hooli');
	});

	it('заголовок сортируемой колонки: кнопка, aria-sort показывает направление', async () => {
		const user = userEvent.setup();
		const { onsort } = renderTable({ sort: { key: 'money', dir: 'desc' } });
		const header = screen.getByRole('columnheader', { name: /💵/ });
		expect(header).toHaveAttribute('aria-sort', 'descending');
		await user.click(within(header).getByRole('button'));
		expect(onsort).toHaveBeenCalledWith('money');
		expect(screen.getByRole('columnheader', { name: /🔋/ })).toHaveAttribute('aria-sort', 'none');
	});

	it('клик по строке открывает аккаунт', async () => {
		const user = userEvent.setup();
		renderTable();
		await user.click(rowOf('Бета'));
		expect(goto).toHaveBeenCalledWith('/a/2');
	});

	it('удаляемый аккаунт не кликабелен', async () => {
		const user = userEvent.setup();
		renderTable();
		await user.click(rowOf('Гамма'));
		expect(goto).not.toHaveBeenCalled();
		expect(within(rowOf('Гамма')).queryByRole('link')).toBeNull();
	});

	it('клик внутри [data-no-row-click] и по ссылке строку не открывает', async () => {
		const user = userEvent.setup();
		renderTable();
		const link = within(rowOf('Бета')).getByRole('link');
		link.addEventListener('click', (e) => e.preventDefault());
		await user.click(link);
		expect(goto).not.toHaveBeenCalled();
		const mark = document.createElement('button');
		mark.setAttribute('data-no-row-click', '');
		rowOf('Бета').querySelector('td')!.append(mark);
		await user.click(mark);
		expect(goto).not.toHaveBeenCalled();
	});

	it('действие бота и причина, по которой оно не исполняется', () => {
		renderTable();
		const bot = within(within(rowOf('Бета')).getByTestId('bot'));
		expect(bot.getByText('⏳ Освободится')).toBeInTheDocument();
		expect(bot.getByText('пауза')).toBeInTheDocument();
	});
});

describe('карточки сводки (телефон)', () => {
	function renderCards() {
		return render(SummaryCards, { rows: rows(), now: NOW });
	}
	const card = (name: string) => screen.getByText(name).closest('li')!;

	it('карточка: имя, уровень, чем занят, цель и первая проблема', () => {
		renderCards();
		const c = within(card('Бета'));
		expect(c.getByText('ур. 12')).toBeInTheDocument();
		expect(c.getByTestId('battle')).toHaveTextContent('🤖Hooli');
		expect(c.getByTestId('problem')).toHaveTextContent('пауза');
		expect(c.getByTestId('activity')).toHaveTextContent('работа');
	});

	it('«Подробнее» раскрывает сетку остальных полей, повторно сворачивает', async () => {
		const user = userEvent.setup();
		renderCards();
		const c = within(card('🤖Альфа'));
		const button = c.getByRole('button', { name: 'Подробнее' });
		expect(button).toHaveAttribute('aria-expanded', 'false');
		expect(c.queryByText('18 450')).toBeNull();
		await user.click(button);
		expect(button).toHaveAttribute('aria-expanded', 'true');
		expect(c.getByText('18 450')).toBeInTheDocument();
		expect(c.getByText('🔨31 🎓28 🐿12 🐢9')).toBeInTheDocument();
		await user.click(button);
		expect(c.queryByText('18 450')).toBeNull();
	});

	it('удаляемый аккаунт без ссылки и без кнопки раскрытия', () => {
		renderCards();
		const c = within(card('Гамма'));
		expect(c.queryByRole('link')).toBeNull();
		expect(c.queryByRole('button')).toBeNull();
	});

	it('устаревший снимок подписан', () => {
		renderCards();
		expect(within(card('Бета')).getByText('снимок от 11:50')).toBeInTheDocument();
	});
});
