import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { OverviewTotals } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';

// В jsdom нет canvas: ECharts подменяется, проверяются настройки, которые ему отдаёт график.
interface BarItem {
	value: number;
	itemStyle?: { decal?: unknown };
}
interface Option {
	series: { type: string; name: string; data: (BarItem | null)[] }[];
	xAxis: { data: string[] };
	tooltip: { formatter: (p: { axisValue: string; seriesName: string; value: number | null }[]) => string };
}
const created: { options: Option[]; disposed: boolean }[] = [];
vi.mock('$lib/metrics/echarts', () => ({
	init: () => {
		const chart = {
			options: [] as Option[],
			disposed: false,
			setOption(o: Option) {
				chart.options.push(o);
			},
			resize() {},
			dispose() {
				chart.disposed = true;
			}
		};
		created.push(chart);
		return chart;
	}
}));

const { default: TotalsCard } = await import('./TotalsCard.svelte');
const { default: TotalsByAccount } = await import('./TotalsByAccount.svelte');
const { default: TotalsChart } = await import('./TotalsChart.svelte');

const data = fixture<OverviewTotals>('overview_totals');
const accounts = [accountOut(1, 'Альфа', { level: 31 }), accountOut(2, 'Бета'), accountOut(3, 'Гамма')];
/** Текст без неразрывных пробелов разрядов. */
const flat = (el: Element) => el.textContent?.replace(/\s+/g, ' ');
const nameOf = (id: number) => accounts.find((a) => a.id === id)?.name ?? `#${id}`;

afterEach(() => {
	cleanup();
	created.length = 0;
});

describe('карточка «Всего»', () => {
	it('изменение по показателям: «по N из M», прочерк без данных', () => {
		render(TotalsCard, { data });
		const card = screen.getByRole('region', { name: 'Всего' });
		const line = (title: string) => within(card).getByTitle(title).textContent?.replace(/\s+/g, ' ').trim();
		expect(line('деньги')).toBe('💵деньги +$91 212 · по 2 из 3');
		expect(line('знания')).toBe('📚знания +75 · по 1 из 3');
		expect(line('личная слава')).toBe('🏆личная слава +55 · по 1 из 3');
	});

	it('без данных ни у одного аккаунта - прочерк, а не ноль', () => {
		const none = { ...data, accounts: [data.accounts[2]!], total: data.accounts[2]!.totals };
		render(TotalsCard, { data: none });
		expect(screen.getByTitle('деньги').textContent).toContain('—');
		expect(screen.getByTitle('деньги').textContent).not.toContain('0');
	});

	it('повышения уровня, 🏆 за задания, предметы, разовое и потери', () => {
		render(TotalsCard, { data });
		const card = screen.getByRole('region', { name: 'Всего' });
		expect(within(card).getByText('+4')).toBeInTheDocument();
		expect(within(card).getByText(/за задания/)).toBeInTheDocument();
		expect(within(card).getByText('270')).toBeInTheDocument();
		expect(within(card).getByText('Нитки ×4')).toBeInTheDocument();
		expect(within(card).getByText(/⚔ битва ×6/)).toBeInTheDocument();
		// Траты и потери: сумма $ и разбивка по видам.
		expect(within(card).getByText('−$1 180')).toBeInTheDocument();
		expect(within(card).getByText(/🛏 отель ×2/)).toBeInTheDocument();
	});

	it('окно длиннее хранения метрик: уровни и баланс - прочерки', () => {
		const blind = {
			...data,
			total: {
				...data.total,
				levels: null,
				balance: Object.fromEntries(
					Object.keys(data.total.balance).map((k) => [k, { delta: null, covered: false, accounts: 0 }])
				) as typeof data.total.balance
			}
		};
		render(TotalsCard, { data: blind });
		expect(screen.getByLabelText('Повышения уровня').textContent).toContain('—');
		expect(screen.getByTitle('опыт').textContent).toContain('—');
	});
});

describe('таблица «По аккаунтам»', () => {
	it('строка на аккаунт с именем-ссылкой на его «Итоги» и числами за период', () => {
		render(TotalsByAccount, { data, accounts });
		const table = screen.getByRole('table', { name: 'По аккаунтам' });
		const rows = within(table).getAllByRole('row').slice(1);
		expect(rows).toHaveLength(3);
		const first = within(rows[0]!);
		expect(first.getByRole('link', { name: 'Альфа' })).toHaveAttribute('href', '/a/1/daily');
		expect(flat(rows[0]!)).toContain('+$60 312');
		expect(flat(rows[0]!)).toContain('+52 200');
		// Нет данных - прочерки, а не нули.
		expect(rows[2]!.textContent).not.toMatch(/\+|\$0/);
		expect(within(rows[2]!).getAllByText('—').length).toBeGreaterThanOrEqual(6);
	});

	it('аккаунт не из списка (список ещё не пришёл) подписан номером', () => {
		render(TotalsByAccount, { data, accounts: null });
		expect(screen.getAllByRole('link', { name: '#2' })[0]).toHaveAttribute('href', '/a/2/daily');
	});

	it('на телефоне то же карточками', () => {
		render(TotalsByAccount, { data, accounts });
		const list = screen.getByRole('list', { name: 'Аккаунты' });
		expect(list.querySelectorAll(':scope > li')).toHaveLength(3);
		expect(within(list).getByText('Альфа')).toBeInTheDocument();
	});
});

describe('график по дням', () => {
	const last = () => created.at(-1)!.options.at(-1)!;

	it('столбец на аккаунт, дни по возрастанию, подписи дд.мм', () => {
		render(TotalsChart, { data, nameOf });
		const o = last();
		expect(o.series.map((s) => [s.type, s.name])).toEqual([
			['bar', 'Альфа'],
			['bar', 'Бета'],
			['bar', 'Гамма']
		]);
		expect(o.xAxis.data).toEqual(['11.10', '12.10', '13.10', '14.10', '15.10', '16.10', '17.10']);
		expect(o.series[0]!.data.map((p) => p?.value ?? null)).toEqual([null, null, null, 15900, 18200, 21400, 4812]);
		expect(screen.getByRole('img', { name: /График: 💵 деньги/ })).toBeInTheDocument();
	});

	it('неполные дни штрихуются, полные нет', () => {
		render(TotalsChart, { data, nameOf });
		const pts = last().series[0]!.data;
		expect(pts[6]!.itemStyle?.decal).toBeTruthy();
		expect(pts[3]!.itemStyle?.decal).toBeTruthy();
		expect(pts[4]!.itemStyle?.decal).toBeUndefined();
		expect(pts[5]!.itemStyle?.decal).toBeUndefined();
	});

	it('переключатель показателя перестраивает график', async () => {
		const user = userEvent.setup();
		render(TotalsChart, { data, nameOf });
		expect(screen.getByRole('button', { name: '💵 деньги' })).toHaveAttribute('aria-pressed', 'true');
		await user.click(screen.getByRole('button', { name: '💡 опыт' }));
		expect(screen.getByRole('button', { name: '💡 опыт' })).toHaveAttribute('aria-pressed', 'true');
		expect(last().series[0]!.data.map((p) => p?.value ?? null)).toEqual([null, null, null, 13800, 14100, 15200, 9100]);
		await user.click(screen.getByRole('button', { name: '🏆 личная слава' }));
		expect(last().series[0]!.data.map((p) => p?.value ?? null)).toEqual([null, null, null, null, null, 55, null]);
	});

	it('подсказка: значения аккаунтов за день, пустые не показываются', () => {
		render(TotalsChart, { data, nameOf });
		const tip = last().tooltip.formatter([
			{ axisValue: '16.10', seriesName: 'Альфа', value: 21400 },
			{ axisValue: '16.10', seriesName: 'Гамма', value: null },
			{ axisValue: '16.10', seriesName: 'Бета', value: 12000 }
		]);
		expect(tip).toContain('16.10');
		expect(tip).toContain('Альфа: <b>$21 400</b>');
		expect(tip).toContain('Бета: <b>$12 000</b>');
		expect(tip).not.toContain('Гамма');
	});

	it('сноска о расхождении суммы по дням с итогом периода', () => {
		render(TotalsChart, { data, nameOf });
		expect(
			screen.getByText('сумма по дням может не сходиться с итогом периода: в дни без изменения значения точек нет')
		).toBeInTheDocument();
	});

	it('график снимается при закрытии и пересобирается на новые данные', async () => {
		const { rerender, unmount } = render(TotalsChart, { data, nameOf });
		const chart = created[0]!;
		const before = chart.options.length;
		await rerender({ data: { ...data, days: 1, accounts: data.accounts.map((a) => ({ ...a, days: a.days.slice(0, 1) })) }, nameOf });
		expect(chart.options.length).toBeGreaterThan(before);
		expect(chart.options.at(-1)!.xAxis.data).toEqual(['17.10']);
		unmount();
		expect(chart.disposed).toBe(true);
	});

	it('нет аккаунтов — графика нет', () => {
		render(TotalsChart, { data: { ...data, accounts: [] }, nameOf });
		expect(created).toHaveLength(0);
		expect(screen.getByText('Нет данных за период.')).toBeInTheDocument();
	});
});
