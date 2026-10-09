import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import type { GridItemHTMLElement } from 'gridstack';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { accounts, current, homeLayout } from '$lib/app.svelte';
import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
import type { LeaveNavigation } from '$lib/settings/leave';
import { dialogs } from '$lib/stores/confirm.svelte';
import { toasts } from '$lib/stores/toasts.svelte';
import { flush } from '$lib/test/deferred';
import { page } from '$lib/test/page.svelte';
import HomeRoute from '../../routes/a/[account]/home-route.test.svelte';
import { BLOCK_TITLES } from './blocks';
import { DEFAULT_LAYOUT, type HomeLayout } from './layout';

const h = vi.hoisted(() => ({
	puts: [] as string[],
	putStatus: 204,
	guards: [] as ((nav: unknown) => void)[]
}));

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({
	goto: vi.fn(async () => {}),
	beforeNavigate: (fn: (nav: unknown) => void) => h.guards.push(fn)
}));
vi.mock('$lib/app.svelte', async () => {
	const { AccountContext, CurrentAccount } = await import('$lib/account.svelte');
	const { AccountsStore } = await import('$lib/stores/accounts.svelte');
	const { HomeLayoutStore } = await import('$lib/home/store.svelte');
	const { createApi } = await import('$lib/api/client');
	const { json, mockFetch } = await import('$lib/test/fetch');
	const { fixture } = await import('$lib/test/fixtures');
	const { FakeSource } = await import('$lib/test/source');
	const hooks = { csrf: () => 'tok', refreshCsrf: async () => 'tok', unauthorized: () => {} };
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
		if (c.url === '/api/v1/me/ui/home-layout' && c.method === 'PUT') {
			h.puts.push(c.body);
			return h.putStatus === 204 ? json(null, 204) : json({ detail: 'store_failed' }, h.putStatus);
		}
		if (c.url === '/api/v1/accounts') return json([account(1)]);
		if (c.url === '/api/v1/accounts/1/engine/status') {
			return json({ ...fixture<object>('engine_status'), running: true, status: 'enabled' });
		}
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

// Ширина сетки: в jsdom медиазапросов нет — подставляется ответ на любой запрос.
function setWide(matches: boolean) {
	window.matchMedia = ((media: string) => ({
		matches,
		media,
		onchange: null,
		addEventListener: () => {},
		removeEventListener: () => {},
		addListener: () => {},
		removeListener: () => {},
		dispatchEvent: () => false
	})) as unknown as typeof window.matchMedia;
}

const block = (id: keyof typeof BLOCK_TITLES) => document.querySelector<GridItemHTMLElement>(`[data-block="${id}"]`)!;

function nav(to = '/a/1/journal') {
	const cancel = vi.fn();
	const n: LeaveNavigation = { type: 'link', willUnload: false, to: { url: new URL(to, 'http://app.invalid') }, cancel };
	return { n, cancel };
}

async function openHome() {
	await accounts.load();
	page.params = { account: '1' };
	render(HomeRoute);
	render(ConfirmDialog);
	await screen.findByRole('heading', { name: 'acc1 · Главная' });
}

beforeEach(() => setWide(true));

afterEach(() => {
	cleanup();
	current.stop();
	homeLayout.stop();
	dialogs.answer(null);
	toasts.items = [];
	h.puts.length = 0;
	h.putStatus = 204;
	h.guards.length = 0;
	vi.mocked(goto).mockClear();
	delete (window as Partial<Window>).matchMedia;
});

describe('главная: режим «Настроить»', () => {
	it('«Настроить» — панель режима, сетка подвижна; «Отмена» — всё как было', async () => {
		const user = userEvent.setup();
		await openHome();
		const grid = document.querySelector('.grid-stack')!;
		expect(grid).toHaveClass('grid-stack-static');
		expect(screen.queryByRole('region', { name: 'Настройка главной' })).toBeNull();

		await user.click(screen.getByRole('button', { name: 'Настроить' }));
		const bar = screen.getByRole('region', { name: 'Настройка главной' });
		expect(grid).not.toHaveClass('grid-stack-static');
		expect(screen.queryByRole('button', { name: 'Настроить' })).toBeNull();

		await user.click(screen.getByRole('button', { name: 'Скрыть «Сбор артефакта»' }));
		expect(block('artifact')).toHaveClass('hidden');
		expect(block('artifact').gridstackNode).toBeUndefined();
		expect(within(bar).getByRole('button', { name: 'Вернуть «Сбор артефакта»' })).toBeInTheDocument();
		expect(homeLayout.dirty).toBe(true);

		await user.click(within(bar).getByRole('button', { name: 'Отмена' }));
		expect(screen.queryByRole('region', { name: 'Настройка главной' })).toBeNull();
		expect(grid).toHaveClass('grid-stack-static');
		expect(block('artifact')).not.toHaveClass('hidden');
		expect(block('artifact').gridstackNode?.id).toBe('artifact');
		expect(homeLayout.layout).toEqual(DEFAULT_LAYOUT);
		expect(h.puts).toEqual([]);
	});

	it('скрыть, вернуть, сбросить — по черновику; «Готово» сохраняет раскладку на сервере', async () => {
		const user = userEvent.setup();
		await openHome();
		await user.click(screen.getByRole('button', { name: 'Настроить' }));
		await user.click(screen.getByRole('button', { name: 'Скрыть «Гаджеты»' }));
		await user.click(screen.getByRole('button', { name: 'Вернуть «Гаджеты»' }));
		expect(block('gadgets')).not.toHaveClass('hidden');
		expect(block('gadgets').gridstackNode?.id).toBe('gadgets');
		await user.click(screen.getByRole('button', { name: 'Скрыть «Сбор артефакта»' }));
		await user.click(screen.getByRole('button', { name: 'Сбросить по умолчанию' }));
		expect(block('artifact')).not.toHaveClass('hidden');
		await user.click(screen.getByRole('button', { name: 'Скрыть «Сбор артефакта»' }));

		await user.click(screen.getByRole('button', { name: 'Готово' }));
		await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'Настройка главной' })).toBeNull());
		expect(h.puts).toHaveLength(1);
		const body = JSON.parse(h.puts[0]!) as HomeLayout;
		expect(body.hidden).toEqual(['artifact']);
		expect(body.items).toHaveLength(6);
		expect(homeLayout.layout.hidden).toEqual(['artifact']);
		expect(block('artifact')).toHaveClass('hidden');
	});

	it('ошибка сохранения — тост, режим правки и раскладка на экране остаются', async () => {
		h.putStatus = 503;
		const user = userEvent.setup();
		await openHome();
		await user.click(screen.getByRole('button', { name: 'Настроить' }));
		await user.click(screen.getByRole('button', { name: 'Скрыть «Сбор артефакта»' }));
		await user.click(screen.getByRole('button', { name: 'Готово' }));
		await vi.waitFor(() => expect(toasts.items.map((t) => t.kind)).toContain('error'));
		expect(toasts.items.find((t) => t.kind === 'error')!.text).toMatch(/^Раскладка не сохранена/);
		expect(screen.getByRole('region', { name: 'Настройка главной' })).toBeInTheDocument();
		expect(block('artifact')).toHaveClass('hidden');
		expect(homeLayout.editing).toBe(true);
		expect(screen.getByRole('button', { name: 'Готово' })).toBeEnabled();
	});

	it('узкий экран: одна колонка по раскладке без скрытых, кнопки «Настроить» нет', async () => {
		setWide(false);
		const l = structuredClone(DEFAULT_LAYOUT) as HomeLayout;
		l.items = l.items.filter((i) => i.id !== 'artifact');
		l.hidden = ['artifact'];
		homeLayout.update(l);
		await openHome();
		expect(document.querySelector('.grid-stack')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Настроить' })).toBeNull();
		expect(screen.queryByRole('region', { name: (n) => n.startsWith(BLOCK_TITLES.artifact) })).toBeNull();
		expect(screen.getByRole('region', { name: (n) => n.startsWith(BLOCK_TITLES.gadgets) })).toBeInTheDocument();
	});
});

describe('главная: уход с несохранённой раскладкой', () => {
	async function dirtyEditing() {
		const user = userEvent.setup();
		await openHome();
		await user.click(screen.getByRole('button', { name: 'Настроить' }));
		await user.click(screen.getByRole('button', { name: 'Скрыть «Сбор артефакта»' }));
		expect(h.guards).toHaveLength(1);
		return user;
	}

	it('без правок — уход без вопросов', async () => {
		const user = userEvent.setup();
		await openHome();
		await user.click(screen.getByRole('button', { name: 'Настроить' }));
		const { n, cancel } = nav();
		h.guards[0]!(n);
		expect(cancel).not.toHaveBeenCalled();
		expect(dialogs.current).toBeNull();
	});

	it('окно с тремя вариантами; «Остаться» — переход отменён, правка продолжается', async () => {
		const user = await dirtyEditing();
		const { n, cancel } = nav();
		h.guards[0]!(n);
		expect(cancel).toHaveBeenCalledTimes(1);
		const dialog = await screen.findByRole('dialog');
		for (const name of ['Сохранить', 'Не сохранять', 'Остаться']) {
			expect(within(dialog).getByRole('button', { name })).toBeInTheDocument();
		}
		await user.click(within(dialog).getByRole('button', { name: 'Остаться' }));
		await flush();
		expect(goto).not.toHaveBeenCalled();
		expect(homeLayout.editing).toBe(true);
		expect(homeLayout.dirty).toBe(true);
	});

	it('«Не сохранять» — черновик сброшен, переход выполняется', async () => {
		const user = await dirtyEditing();
		h.guards[0]!(nav().n);
		await user.click(await screen.findByRole('button', { name: 'Не сохранять' }));
		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith(new URL('http://app.invalid/a/1/journal')));
		expect(homeLayout.editing).toBe(false);
		expect(homeLayout.layout).toEqual(DEFAULT_LAYOUT);
		expect(h.puts).toEqual([]);
	});

	it('«Сохранить» — раскладка на сервере, переход выполняется', async () => {
		const user = await dirtyEditing();
		h.guards[0]!(nav().n);
		await user.click(await screen.findByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(goto).toHaveBeenCalledTimes(1));
		expect(h.puts).toHaveLength(1);
		expect(homeLayout.layout.hidden).toEqual(['artifact']);
	});

	it('«Сохранить» с ошибкой — тост, переход отменён, правка продолжается', async () => {
		h.putStatus = 503;
		const user = await dirtyEditing();
		h.guards[0]!(nav().n);
		await user.click(await screen.findByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(toasts.items.map((t) => t.kind)).toContain('error'));
		await flush();
		expect(goto).not.toHaveBeenCalled();
		expect(homeLayout.editing).toBe(true);
		expect(homeLayout.dirty).toBe(true);
	});

	it('после «Не сохранять» и перехода на тот же адрес следующий уход с правками снова спрашивает', async () => {
		const user = await dirtyEditing();
		h.guards[0]!(nav('/a/1').n);
		await user.click(await screen.findByRole('button', { name: 'Не сохранять' }));
		await vi.waitFor(() => expect(goto).toHaveBeenCalledTimes(1));
		// goto на тот же адрес — страница остаётся, её обработчик видит этот переход.
		const same = nav('/a/1');
		h.guards[0]!(same.n);
		expect(same.cancel).not.toHaveBeenCalled();

		await user.click(screen.getByRole('button', { name: 'Настроить' }));
		await user.click(screen.getByRole('button', { name: 'Скрыть «Гаджеты»' }));
		const next = nav('/a/1/journal');
		h.guards[0]!(next.n);
		expect(next.cancel).toHaveBeenCalledTimes(1);
		expect(await screen.findByRole('dialog')).toBeInTheDocument();
		expect(homeLayout.dirty).toBe(true);
	});
});

