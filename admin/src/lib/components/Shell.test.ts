import { cleanup, render, screen } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { accounts } from '$lib/app.svelte';
import type { AccountOut } from '$lib/api/types';
import { page } from '$lib/test/page.svelte';
import Shell from './Shell.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$lib/app.svelte', () => ({
	accounts: { list: null as AccountOut[] | null },
	current: { ctx: null },
	session: { login: 'admin' }
}));

const account = (id: number): AccountOut => ({
	id,
	name: `acc${id}`,
	status: 'enabled',
	status_reason: null,
	tg: { user_id: id, online: true },
	mode: 'dry_run',
	paused: false,
	killed: false,
	last_action_at: null,
	unread: { warn: 0, error: 0 }
});

const children = createRawSnippet(() => ({ render: () => '<p>экран</p>' }));
const accountLinks = () =>
	screen
		.getAllByRole('link')
		.map((a) => a.getAttribute('href')!)
		.filter((href) => href.startsWith('/a/'));

function open(pathname: string, params: Record<string, string>) {
	page.params = params;
	page.url = new URL(pathname, 'http://app.invalid');
	render(Shell, { children });
}

afterEach(() => {
	cleanup();
	localStorage.clear();
	accounts.list = null;
});

describe('меню оболочки и список аккаунтов', () => {
	it('аккаунт из адреса, пока он в списке, — меню ведёт на его экраны', () => {
		accounts.list = [account(1), account(2)];
		open('/a/2/journal', { account: '2' });
		expect(accountLinks().filter((href) => href.startsWith('/a/2')).length).toBeGreaterThan(8);
	});

	it('аккаунта нет в списке — меню не ведёт на его экраны ни из адреса, ни из localStorage', () => {
		accounts.list = [account(1)];
		localStorage.setItem('pyrobot.account', '2');
		// Общий экран, контекст остановлен: меню — первый из оставшихся.
		open('/accounts', {});
		const links = accountLinks();
		expect(links.length).toBeGreaterThan(8);
		expect(links.every((href) => href === '/a/1' || href.startsWith('/a/1/'))).toBe(true);
		cleanup();

		// Адрес ещё удалённого аккаунта (до перехода на /accounts).
		open('/a/2/journal', { account: '2' });
		expect(accountLinks().every((href) => href === '/a/1' || href.startsWith('/a/1/'))).toBe(true);
	});
});
