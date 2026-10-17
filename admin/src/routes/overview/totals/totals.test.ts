import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import { flushSync } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AccountOut, OverviewTotals } from '$lib/api/types';
import type { Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import { page } from '$lib/test/page.svelte';
import Page from './+page.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$lib/metrics/echarts', () => ({
	init: () => ({ setOption() {}, resize() {}, dispose() {} })
}));

const h = vi.hoisted(() => ({
	body: null as unknown,
	status: 200,
	calls: [] as Call[],
	list: null as unknown
}));

vi.mock('$lib/app.svelte', async () => {
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const hooks = { csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} };
	const fetch = mockFetch((call) => {
		h.calls.push(call);
		if (call.url.startsWith('/api/v1/overview/totals')) return json(h.body, h.status);
		return json({ detail: 'not found' }, 404);
	});
	return {
		api: createApi(hooks, fetch),
		accounts: {
			get list() {
				return h.list;
			},
			error: null,
			load: async () => {}
		}
	};
});

const week = fixture<OverviewTotals>('overview_totals');
const ACCOUNTS: AccountOut[] = [accountOut(1, 'Альфа'), accountOut(2, 'Бета'), accountOut(3, 'Гамма')];
const asked = () => h.calls.map((c) => new URL(c.url, 'http://x').searchParams.get('days'));

function show(url = '/overview/totals', body: unknown = week) {
	page.url = new URL(url, 'http://app.invalid');
	h.body = body;
	h.list = ACCOUNTS;
	return render(Page);
}

beforeEach(() => {
	h.calls.length = 0;
});
afterEach(() => {
	cleanup();
	h.status = 200;
});

describe('экран «Итоги»', () => {
	it('без ?days= - 7 дней: переключатель, карточка «Всего», таблица и график', async () => {
		show();
		expect(await screen.findByRole('region', { name: 'Всего' })).toBeInTheDocument();
		expect(asked()).toEqual(['7']);
		const switcher = screen.getByRole('navigation', { name: 'Период' });
		expect(within(switcher).getAllByRole('link').map((a) => a.textContent)).toEqual(['Сегодня', '7 дней', '30 дней']);
		expect(within(switcher).getByRole('link', { name: '7 дней' })).toHaveAttribute('aria-current', 'page');
		expect(within(switcher).getByRole('link', { name: '30 дней' })).toHaveAttribute('href', '?days=30');
		expect(screen.getByRole('table', { name: 'По аккаунтам' })).toBeInTheDocument();
		expect(screen.getByRole('img', { name: /График: 💵 деньги/ })).toBeInTheDocument();
		expect(within(screen.getByRole('table', { name: 'По аккаунтам' })).getByRole('link', { name: 'Альфа' })).toBeInTheDocument();
	});

	it('пока список аккаунтов не пришёл, вместо «#id» - ожидание; имена появляются сразу', async () => {
		page.url = new URL('/overview/totals', 'http://app.invalid');
		h.body = week;
		h.list = null;
		render(Page);
		await waitFor(() => expect(asked()).toEqual(['7']));
		expect(await screen.findByRole('status')).toHaveTextContent(/^Загрузка…$/);
		expect(screen.queryByRole('region', { name: 'Всего' })).toBeNull();
		expect(screen.queryByText(/#\d/)).toBeNull();
	});

	it('?days=30 берётся из адреса; смена адреса перечитывает другой период', async () => {
		show('/overview/totals?days=30', { ...week, days: 30 });
		expect(await screen.findByRole('region', { name: 'Всего' })).toBeInTheDocument();
		expect(asked()).toEqual(['30']);
		expect(screen.getByRole('link', { name: '30 дней' })).toHaveAttribute('aria-current', 'page');
		h.body = { ...week, days: 1, first: week.today };
		page.url = new URL('http://app.invalid/overview/totals?days=1');
		flushSync();
		await waitFor(() => expect(asked()).toEqual(['30', '1']));
		expect(await screen.findByRole('link', { name: 'Сегодня' })).toHaveAttribute('aria-current', 'page');
	});

	it('мусор в ?days= - 7 дней', async () => {
		show('/overview/totals?days=5');
		await screen.findByRole('region', { name: 'Всего' });
		expect(asked()).toEqual(['7']);
	});

	it('нет аккаунтов: пустое состояние со ссылкой на «Аккаунты»', async () => {
		show('/overview/totals', { ...week, accounts: [], total: { ...week.total, levels: 0 } });
		const empty = await screen.findByRole('region', { name: 'Нет аккаунтов' });
		expect(within(empty).getByRole('link', { name: 'Открыть аккаунты' })).toHaveAttribute('href', '/accounts');
		expect(screen.queryByRole('region', { name: 'Всего' })).toBeNull();
	});

	it('окно длиннее хранения метрик - пояснение', async () => {
		show('/overview/totals?days=30', { ...week, days: 30, first: '2026-09-18', metrics_floor: '2026-10-07T09:00:00+00:00' });
		expect(await screen.findByText(/Метрики хранятся 10 дней/)).toBeInTheDocument();
	});

	it('ошибка без данных - сообщение; с данными - прежние данные и предупреждение', async () => {
		h.status = 500;
		show('/overview/totals', { detail: 'boom' });
		expect(await screen.findByRole('alert')).toHaveTextContent('Итоги недоступны');
	});

	it('опрос останавливается при уходе с экрана', async () => {
		vi.useFakeTimers({ toFake: ['setInterval', 'setTimeout', 'clearInterval', 'clearTimeout'] });
		try {
			const view = show('/overview/totals?days=1', { ...week, days: 1 });
			await vi.advanceTimersByTimeAsync(0);
			expect(asked()).toEqual(['1']);
			view.unmount();
			await vi.advanceTimersByTimeAsync(600_000);
			expect(asked()).toEqual(['1']);
		} finally {
			vi.useRealTimers();
		}
	});
});
