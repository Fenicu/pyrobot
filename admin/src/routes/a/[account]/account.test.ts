import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { accounts, current } from '$lib/app.svelte';
import { BLOCK_IDS, BLOCK_TITLES } from '$lib/home/blocks';
import { DEFAULT_LAYOUT, phoneOrder } from '$lib/home/layout';
import { page } from '$lib/test/page.svelte';
import HomeRoute from './home-route.test.svelte';
import JournalRoute from './journal-route.test.svelte';

const h = vi.hoisted(() => ({ calls: [] as string[], patches: [] as string[], down: false }));

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}), beforeNavigate: () => {} }));
// Синглтоны вкладки — настоящие классы, но на подделках fetch и EventSource.
vi.mock('$lib/app.svelte', async () => {
	const { AccountContext, CurrentAccount } = await import('$lib/account.svelte');
	const { AccountsStore } = await import('$lib/stores/accounts.svelte');
	const { HomeLayoutStore } = await import('$lib/home/store.svelte');
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
		if (c.method === 'PATCH') {
			h.patches.push(`${c.url} ${c.body}`);
			h.down = false;
			return json(account(1));
		}
		if (c.url === '/api/v1/accounts') return json([account(1), account(2)]);
		if (c.url === '/api/v1/accounts/1/engine/status') {
			const status = fixture<object>('engine_status');
			return json(
				h.down
					? { ...status, running: false, status: 'disabled', status_reason: null, host_reason: null }
					: { ...status, running: true, status: 'enabled', status_reason: null, host_reason: null }
			);
		}
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
	const api = createApi(hooks, fetch);
	return {
		accounts: new AccountsStore(api),
		api,
		current,
		homeLayout: new HomeLayoutStore(api),
		session: { status: 'authenticated' },
		startAccount: (id: number) => current.start(id)
	};
});

const journal = (id: number) => h.calls.filter((u) => u.startsWith(`/api/v1/accounts/${id}/journal`)).length;

afterEach(() => {
	// Сначала убрать экран: смонтированный макет открыл бы аккаунт снова.
	cleanup();
	current.stop();
	h.calls.length = 0;
	h.patches.length = 0;
	h.down = false;
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
		expect(screen.getByRole('heading', { name: 'acc2 · Журнал' })).toBeInTheDocument();
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
			expect(screen.queryByRole('heading', { name: /Журнал/ })).toBeNull();
			unmount();
			vi.mocked(goto).mockClear();
		}
	});

	it('аккаунт без движка: плашка под шапкой, включение перечитывает статус и список', async () => {
		h.down = true;
		await accounts.load();
		page.params = { account: '1' };
		render(JournalRoute);
		const banner = await screen.findByText('Движок не запущен: аккаунт выключен');
		const heading = screen.getByRole('heading', { name: 'acc1 · Журнал' });
		// Статус движка — в шапке любого раздела, не только главной.
		expect(heading.closest('header')).toContainElement(screen.getByRole('region', { name: 'Статус' }));
		expect(screen.getByRole('region', { name: 'Статус' })).toHaveTextContent('движок не запущен');
		expect(heading.compareDocumentPosition(banner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

		const lists = h.calls.filter((u) => u === '/api/v1/accounts').length;
		await userEvent.setup().click(screen.getByRole('button', { name: 'Включить' }));
		await vi.waitFor(() => expect(screen.queryByText(/Движок не запущен/)).toBeNull());
		expect(h.patches).toEqual(['/api/v1/accounts/1 {"enabled":true}']);
		expect(h.calls.filter((u) => u === '/api/v1/accounts').length).toBe(lists + 1);
	});

	it('главная — семь блоков одной колонкой в порядке раскладки, без отдельного блока «Управление»', async () => {
		await accounts.load();
		page.params = { account: '1' };
		render(HomeRoute);
		const home = await screen.findByRole('heading', { name: 'acc1 · Главная' });
		const main = home.closest('header')!.parentElement!;
		// Узкий экран (в jsdom медиазапросов нет): сверху вниз, в ряду — слева направо.
		const blocks = phoneOrder(DEFAULT_LAYOUT).map((id) =>
			within(main).getByRole('region', { name: (name) => name.startsWith(BLOCK_TITLES[id]) })
		);
		expect(blocks).toHaveLength(BLOCK_IDS.length);
		for (let i = 1; i < blocks.length; i++) {
			expect(blocks[i - 1]!.compareDocumentPosition(blocks[i]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		}
		expect(screen.queryByRole('region', { name: 'Управление' })).toBeNull();
		expect(screen.queryByRole('region', { name: /План бота|Метро — прохождение/ })).toBeNull();
		// Пауза, режим и kill — в шапке страницы.
		expect(await within(home.closest('header')!).findByRole('group', { name: 'Управление' })).toBeInTheDocument();
	});
});
