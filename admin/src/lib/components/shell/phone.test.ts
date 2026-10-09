import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { accounts, current, session } from '$lib/app.svelte';
import type { AccountOut } from '$lib/api/types';
import { dialogs } from '$lib/stores/confirm.svelte';
import { theme } from '$lib/stores/theme.svelte';
import { page } from '$lib/test/page.svelte';
import Shell from '../Shell.svelte';
import PhonePage from './phone-page.test.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));
vi.mock('$lib/app.svelte', () => ({
	accounts: { list: null as AccountOut[] | null, load: async () => {} },
	api: {},
	current: { ctx: null as unknown },
	session: { login: 'admin', role: 'user' as 'owner' | 'user' | null, signOut: vi.fn(async () => null) }
}));

const account = (id: number, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name: `acc${id}`,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: id, online: true },
	mode: 'live',
	paused: false,
	killed: false,
	last_action_at: null,
	unread: { warn: 0, error: 0 },
	company: null,
	team_tag: null,
	level: null,
	busy: null,
	in_metro: false,
	alert: null,
	...over
});

const children = createRawSnippet(() => ({ render: () => '<p>экран</p>' }));

function at(pathname: string, params: Record<string, string> = {}) {
	page.params = params;
	page.url = new URL(pathname, 'http://app.invalid');
}

function open(pathname: string, params: Record<string, string> = {}) {
	at(pathname, params);
	return render(Shell, { children });
}

function openCtx(id: number, unread: number, mode: 'live' | 'dry_run' = 'live') {
	current.ctx = {
		id,
		live: { status: 'open', retryIn: 0 },
		unread: { count: unread },
		engine: { status: { mode, running: true } }
	} as unknown as typeof current.ctx;
}

const tabs = () => screen.getByRole('navigation', { name: 'Вкладки' });
const topBar = () => screen.getByRole('banner', { name: 'Открытый экран' });

async function openMenu() {
	const user = userEvent.setup();
	await user.click(within(tabs()).getByRole('button', { name: /^Меню/ }));
	return { user, menu: screen.getByRole('dialog') };
}

afterEach(() => {
	cleanup();
	localStorage.clear();
	accounts.list = null;
	current.ctx = null;
	session.role = 'user';
	vi.restoreAllMocks();
});

describe('телефон: нижние вкладки', () => {
	it('Аккаунты, открытый аккаунт, Журнал, Управление и кнопка «Меню»; активная отмечена', () => {
		accounts.list = [account(1, { name: 'Iko', company: 'bmesa', team_tag: 'LA' }), account(2)];
		open('/a/1/journal', { account: '1' });
		const links = within(tabs()).getAllByRole('link');
		expect(links.map((a) => a.getAttribute('href'))).toEqual(['/accounts', '/a/1', '/a/1/journal', '/a/1/control']);
		// Вкладка аккаунта — его имя без значка компании и тега: место в панели узкое.
		expect(links.map((a) => a.textContent?.trim())).toEqual(['Аккаунты', 'Iko', 'Журнал', 'Управл.']);
		expect(links[2]).toHaveAttribute('aria-current', 'page');
		expect(links[1]).not.toHaveAttribute('aria-current');
		const menu = within(tabs()).getByRole('button', { name: 'Меню' });
		expect(menu).toHaveAttribute('aria-expanded', 'false');
	});

	it('без аккаунтов — только «Аккаунты» и «Меню»', () => {
		accounts.list = [];
		open('/accounts');
		const links = within(tabs()).getAllByRole('link');
		expect(links.map((a) => a.getAttribute('href'))).toEqual(['/accounts']);
		expect(links[0]).toHaveAttribute('aria-current', 'page');
		expect(within(tabs()).getByRole('button', { name: 'Меню' })).toBeInTheDocument();
	});

	it('у «Аккаунтов» — число аккаунтов, требующих внимания', () => {
		accounts.list = [
			account(1),
			account(2, { paused: true }),
			account(3, { status: 'error' }),
			account(4, { status: 'disabled', blocked: true }),
			account(5, { tg: { user_id: 5, online: false } })
		];
		open('/a/1', { account: '1' });
		const link = within(tabs()).getByRole('link', { name: /^Аккаунты/ });
		expect(link).toHaveTextContent('3');
		expect(link).toHaveAccessibleDescription('требуют внимания: 3');
		cleanup();

		accounts.list = [account(1), account(2, { status: 'disabled' })];
		open('/a/1', { account: '1' });
		const quiet = within(tabs()).getByRole('link', { name: 'Аккаунты' });
		expect(quiet).toHaveTextContent(/^Аккаунты$/);
		expect(quiet).not.toHaveAccessibleDescription();
	});

	it('старой плашки аккаунта сверху и «Ещё» больше нет', () => {
		accounts.list = [account(1), account(2)];
		open('/a/1', { account: '1' });
		expect(screen.queryByRole('button', { name: /Ещё/ })).toBeNull();
		expect(screen.queryByRole('button', { name: /acc1/ })).toBeNull();
	});
});

describe('телефон: меню', () => {
	it('плитки разделов аккаунта под его именем; у «Уведомлений» — непрочитанные', async () => {
		accounts.list = [account(1, { name: 'Iko', company: 'bmesa', team_tag: 'LA' })];
		openCtx(1, 3);
		open('/a/1', { account: '1' });
		// Непрочитанные видны и на закрытой кнопке.
		expect(within(tabs()).getByRole('button', { name: /^Меню/ })).toHaveAccessibleDescription(
			'непрочитанных предупреждений и ошибок: 3'
		);
		const { menu } = await openMenu();
		expect(menu).toHaveAccessibleName('☣️[LA] Iko');
		const section = within(menu).getByRole('list', { name: 'Разделы аккаунта' });
		const links = within(section).getAllByRole('link');
		expect(links.map((a) => [a.textContent?.trim(), a.getAttribute('href')])).toEqual([
			['Метро', '/a/1/metro'],
			['Итоги', '/a/1/daily'],
			['Метрики', '/a/1/metrics'],
			['Настройки', '/a/1/settings'],
			['Уведомления 3', '/a/1/notifications'],
			['Telegram', '/a/1/telegram']
		]);
	});

	it('«Общее»: Сервер только владельцу, пароль, тема по кругу', async () => {
		accounts.list = [account(1)];
		open('/a/1', { account: '1' });
		let { menu, user } = await openMenu();
		let common = within(menu).getByRole('list', { name: 'Общее' });
		expect(within(common).queryByRole('link', { name: 'Сервер' })).toBeNull();
		expect(within(common).getByRole('link', { name: 'Пароль и коды' })).toHaveAttribute('href', '/password');

		theme.set('dark');
		await user.click(within(common).getByRole('button', { name: 'Тема: тёмная' }));
		expect(theme.pref).toBe('light');
		await user.click(within(common).getByRole('button', { name: 'Тема: светлая' }));
		expect(theme.pref).toBe('system');
		await user.click(within(common).getByRole('button', { name: 'Тема: как в системе' }));
		expect(theme.pref).toBe('dark');
		cleanup();

		session.role = 'owner';
		open('/a/1', { account: '1' });
		({ menu } = await openMenu());
		common = within(menu).getByRole('list', { name: 'Общее' });
		expect(within(common).getByRole('link', { name: 'Сервер' })).toHaveAttribute('href', '/admin');
	});

	it('без аккаунтов — только «Общее»', async () => {
		accounts.list = [];
		open('/accounts');
		const { menu } = await openMenu();
		expect(menu).toHaveAccessibleName('Меню');
		expect(within(menu).queryByRole('list', { name: 'Разделы аккаунта' })).toBeNull();
		expect(within(menu).getByRole('list', { name: 'Общее' })).toBeInTheDocument();
	});

	it('переход по плитке закрывает меню', async () => {
		accounts.list = [account(1)];
		open('/a/1', { account: '1' });
		const { menu, user } = await openMenu();
		await user.click(within(menu).getByRole('link', { name: 'Метро' }));
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('внизу логин, версия и выход с подтверждением', async () => {
		const confirm = vi.spyOn(dialogs, 'confirm').mockResolvedValue(false);
		accounts.list = [account(1)];
		open('/a/1', { account: '1' });
		let { menu, user } = await openMenu();
		expect(menu).toHaveTextContent('admin');
		expect(within(menu).getByRole('link', { name: 'v0.0.0-dev' })).toHaveAttribute('href', '/changes');
		await user.click(within(menu).getByRole('button', { name: 'Выйти' }));
		expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Выйти из админки?' }));
		expect(session.signOut).not.toHaveBeenCalled();

		confirm.mockResolvedValue(true);
		({ menu, user } = await openMenu());
		await user.click(within(menu).getByRole('button', { name: 'Выйти' }));
		expect(session.signOut).toHaveBeenCalledOnce();
	});
});

describe('телефон: верхняя полоса', () => {
	it('экран аккаунта — точка, имя · раздел, LIVE и непрочитанные', () => {
		accounts.list = [account(1, { name: 'Iko', company: 'bmesa', team_tag: 'LA', unread: { warn: 1, error: 0 } })];
		openCtx(1, 4);
		at('/a/1/journal', { account: '1' });
		render(PhonePage, { title: 'Журнал' });
		const bar = topBar();
		expect(within(bar).getByRole('img')).toHaveAccessibleName('требует внимания');
		expect(bar).toHaveTextContent('☣️[LA] Iko · Журнал');
		expect(within(bar).getByText('LIVE')).toBeInTheDocument();
		// Счётчик — из потока открытого аккаунта, свежее списка; ведёт в уведомления.
		const unread = within(bar).getByRole('link', { name: 'непрочитанных предупреждений и ошибок: 4' });
		expect(unread).toHaveAttribute('href', '/a/1/notifications');
		expect(unread).toHaveTextContent('4');
	});

	it('dry_run и без непрочитанных', () => {
		accounts.list = [account(1, { mode: 'dry_run' })];
		at('/a/1', { account: '1' });
		render(PhonePage, { title: 'Главная' });
		const bar = topBar();
		expect(bar).toHaveTextContent('acc1 · Главная');
		expect(within(bar).getByText('DRY RUN')).toBeInTheDocument();
		expect(within(bar).queryByRole('link')).toBeNull();
	});

	it('общая страница — только её название', () => {
		accounts.list = [account(1)];
		at('/password');
		render(PhonePage, { title: 'Пароль и коды' });
		const bar = topBar();
		expect(bar).toHaveTextContent(/^Пароль и коды$/);
		expect(within(bar).queryByRole('img')).toBeNull();
	});
});
