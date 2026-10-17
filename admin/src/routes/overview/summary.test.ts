import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AccountOut, OverviewAccount } from '$lib/api/types';
import { OverviewAccountsStore } from '$lib/overview/accounts.svelte';
import { SORT_KEY } from '$lib/overview/table';
import { fixture } from '$lib/test/fixtures';
import { accountOut } from '$lib/test/overview';
import Page from './+page.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const h = vi.hoisted(() => ({
	extra: null as unknown,
	getStatus: 200,
	list: null as unknown,
	listError: null as unknown
}));

vi.mock('$lib/app.svelte', async () => {
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const hooks = { csrf: () => 'tok', refreshCsrf: async () => null, unauthorized: () => {} };
	const fetch = mockFetch((call) => {
		if (call.url === '/api/v1/overview/accounts') return json(h.extra, h.getStatus);
		return json({ detail: 'not found' }, 404);
	});
	return {
		api: createApi(hooks, fetch),
		accounts: {
			get list() {
				return h.list;
			},
			get error() {
				return h.listError;
			},
			load: async () => {}
		}
	};
});

const extras = fixture<OverviewAccount[]>('overview_accounts');
const ACCOUNTS: AccountOut[] = [
	accountOut(1, 'Альфа', { level: 31 }),
	accountOut(2, 'Бета', { level: 12 }),
	accountOut(3, 'Гамма', { level: 4 }),
	accountOut(4, 'Дельта')
];

function show(list: AccountOut[] | null = ACCOUNTS, extra: unknown = extras) {
	h.list = list;
	h.extra = extra;
	return render(Page);
}

/** Имена аккаунтов в таблице сверху вниз. */
const tableNames = () =>
	within(screen.getByRole('table'))
		.getAllByRole('row')
		.slice(1, -1)
		.map((r) => within(r).getAllByRole('link')[0]?.textContent);

beforeEach(() => localStorage.clear());
afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
	h.getStatus = 200;
	h.listError = null;
});

describe('экран «Сводка»', () => {
	it('таблица для десктопа и карточки для телефона из одних данных', async () => {
		show();
		expect(await screen.findByRole('table')).toBeInTheDocument();
		expect(tableNames()).toEqual(['Альфа', 'Бета', 'Гамма', 'Дельта']);
		expect(screen.getByRole('list', { name: 'Аккаунты' })).toBeInTheDocument();
	});

	it('пока список или сводка не пришли - «Загрузка»', () => {
		show(null);
		expect(screen.getByRole('status')).toHaveTextContent('Загрузка');
		expect(screen.queryByRole('table')).toBeNull();
	});

	it('нет аккаунтов - ссылка на список', async () => {
		show([], []);
		const link = await screen.findByRole('link', { name: 'Открыть аккаунты' });
		expect(link).toHaveAttribute('href', '/accounts');
		expect(screen.queryByRole('table')).toBeNull();
	});

	it('сводка недоступна: таблица без чисел и предупреждение', async () => {
		h.getStatus = 500;
		show(ACCOUNTS, { detail: 'boom' });
		expect(await screen.findByRole('alert')).toHaveTextContent('Сводка недоступна');
		expect(screen.getByRole('table')).toBeInTheDocument();
	});

	it('сортировка по клику запоминается и применяется при следующем входе', async () => {
		const user = userEvent.setup();
		show();
		await screen.findByRole('table');
		const header = screen.getByRole('columnheader', { name: /💵/ });
		await user.click(within(header).getByRole('button'));
		// деньги по возрастанию: 800, 5200, 18450, без снимка в конце
		expect(tableNames()).toEqual(['Гамма', 'Бета', 'Альфа', 'Дельта']);
		expect(JSON.parse(localStorage.getItem(SORT_KEY)!)).toEqual({ key: 'money', dir: 'asc' });
		await user.click(within(header).getByRole('button'));
		expect(tableNames()).toEqual(['Альфа', 'Бета', 'Гамма', 'Дельта']);
		await user.click(within(header).getByRole('button'));
		expect(localStorage.getItem(SORT_KEY)).toBeNull();
		cleanup();
		localStorage.setItem(SORT_KEY, JSON.stringify({ key: 'money', dir: 'asc' }));
		show();
		await screen.findByRole('table');
		expect(tableNames()).toEqual(['Гамма', 'Бета', 'Альфа', 'Дельта']);
	});

	it('опрос сводки запускается при входе и останавливается при уходе', async () => {
		const start = vi.spyOn(OverviewAccountsStore.prototype, 'start');
		const stop = vi.spyOn(OverviewAccountsStore.prototype, 'stop');
		const view = show();
		await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
		expect(stop).not.toHaveBeenCalled();
		view.unmount();
		expect(stop).toHaveBeenCalledTimes(1);
	});
});
