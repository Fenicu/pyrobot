import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { accounts, current } from '$lib/app.svelte';
import { page } from '$lib/test/page.svelte';
import JournalRoute from './journal-route.test.svelte';

const h = vi.hoisted(() => ({ calls: [] as string[] }));

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}), beforeNavigate: () => {} }));
// Синглтоны вкладки — настоящие классы, но на подделках fetch и EventSource.
vi.mock('$lib/app.svelte', async () => {
	const { AccountContext, CurrentAccount } = await import('$lib/account.svelte');
	const { AccountsStore } = await import('$lib/stores/accounts.svelte');
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const { fixture } = await import('$lib/test/fixtures');
	const { FakeSource } = await import('$lib/test/source');
	const hooks = { csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} };
	const account = (id: number) => ({
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
	const fetch = mockFetch((c) => {
		h.calls.push(c.url);
		if (c.url === '/api/v1/accounts') return json([account(1), account(2)]);
		if (/^\/api\/v1\/accounts\/\d+\/journal/.test(c.url)) return json(fixture('journal_page'));
		return json({ detail: 'engine not running' }, 503);
	});
	const current = new CurrentAccount(
		(id) =>
			new AccountContext(id, {
				hooks,
				fetch,
				createSource: (url) => new FakeSource(url),
				checkSession: async () => 'ok',
				onUnauthorized: () => {}
			})
	);
	return {
		accounts: new AccountsStore(createApi(hooks, fetch)),
		current,
		startAccount: (id: number) => current.start(id)
	};
});

const journal = (id: number) => h.calls.filter((u) => u.startsWith(`/api/v1/accounts/${id}/journal`)).length;

afterEach(() => {
	// Сначала убрать экран: смонтированный макет открыл бы аккаунт снова.
	cleanup();
	current.stop();
	h.calls.length = 0;
	vi.mocked(goto).mockClear();
	localStorage.clear();
});

describe('экраны аккаунта /a/[account]', () => {
	it('смена аккаунта в адресе пересоздаёт JournalFeed: журнал — с нового аккаунта', async () => {
		await accounts.load();
		page.params = { account: '1' };
		render(JournalRoute);
		await vi.waitFor(() => expect(journal(1)).toBe(1));
		expect(current.ctx?.id).toBe(1);
		expect(localStorage.getItem('pyrobot.account')).toBe('1');
		const first = current.ctx;

		page.params = { account: '2' };
		await vi.waitFor(() => expect(journal(2)).toBe(1));
		expect(journal(1)).toBe(1);
		expect(current.ctx?.id).toBe(2);
		expect(first?.live.status).toBe('idle');
		expect(localStorage.getItem('pyrobot.account')).toBe('2');
		expect(screen.getByRole('heading', { name: 'Журнал' })).toBeInTheDocument();
		expect(goto).not.toHaveBeenCalled();
	});

	it('аккаунт в адресе не число или не из списка — на /accounts', async () => {
		await accounts.load();
		for (const bad of ['x', '9']) {
			page.params = { account: bad };
			const { unmount } = render(JournalRoute);
			await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/accounts', { replaceState: true }));
			expect(current.ctx).toBeNull();
			expect(journal(9)).toBe(0);
			expect(screen.queryByRole('heading', { name: 'Журнал' })).toBeNull();
			unmount();
			vi.mocked(goto).mockClear();
		}
	});
});
