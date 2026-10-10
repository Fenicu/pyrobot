import { cleanup, render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { accounts, current, session } from '$lib/app.svelte';
import type { AccountOut, EngineStatus } from '$lib/api/types';
import { dialogs } from '$lib/stores/confirm.svelte';
import { theme } from '$lib/stores/theme.svelte';
import { fixture } from '$lib/test/fixtures';
import { page } from '$lib/test/page.svelte';
import Shell from '../Shell.svelte';
import { accountFrameContext, type AccountFrame } from './frame';
import Page from './Page.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));
vi.mock('$lib/app.svelte', () => ({
	accounts: { list: null as AccountOut[] | null, load: async () => {} },
	api: {},
	current: { ctx: null as unknown },
	session: { login: 'admin', role: 'user' as 'owner' | 'user' | null, signOut: vi.fn(async () => null) }
}));

const FAR = '2099-01-01T00:00:00Z';

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

function open(pathname: string, params: Record<string, string> = {}) {
	page.params = params;
	page.url = new URL(pathname, 'http://app.invalid');
	return render(Shell, { children });
}

const column = () => screen.getByRole('complementary', { name: 'Аккаунты' });
const rail = () => screen.getByRole('navigation', { name: 'Полоса разделов' });

afterEach(() => {
	cleanup();
	localStorage.clear();
	accounts.list = null;
	current.ctx = null;
	session.role = 'user';
	vi.restoreAllMocks();
});

describe('колонка аккаунтов', () => {
	it('аккаунты по порядку списка: точка тона, титул, уровень и чем занят', () => {
		accounts.list = [
			account(1, { level: 54, busy: { activity: 'learn', until: FAR }, company: 'bmesa', team_tag: 'LA' }),
			account(2, { level: 41, paused: true }),
			account(3, { status: 'error', status_reason: 'сессия отозвана' }),
			account(4, { status: 'disabled' })
		];
		open('/a/1', { account: '1' });
		const rows = within(column()).getAllByRole('listitem');
		expect(rows.map((r) => within(r).getByRole('img').getAttribute('aria-label'))).toEqual([
			'работает',
			'требует внимания',
			'ошибка',
			'выключен'
		]);
		expect(rows[0]).toHaveTextContent('☣️[LA] acc1');
		expect(rows[0]).toHaveTextContent('54 · учёба');
		expect(rows[1]).toHaveTextContent('41 · пауза');
		// Без уровня — только состояние.
		expect(rows[2]).toHaveTextContent('сессия отозвана');
		expect(rows[2]).not.toHaveTextContent('·');
		expect(rows[3]).toHaveTextContent('выключен');
	});

	it('две строки: имя целиком, ниже уровень и занятость; title содержит всё', () => {
		accounts.list = [
			account(1, { name: '☣️ [SU] Fenicu', level: 71 }),
			account(2, {
				name: '☣️ [LA] Iko',
				level: 54,
				busy: { activity: 'learn', until: FAR },
				tg: { user_id: 2, online: false }
			}),
			account(3, { name: '☣️ [SU] Khalida', status: 'deleting' })
		];
		open('/a/1', { account: '1' });
		const links = within(column()).getAllByRole('link', { name: /Fenicu|Iko/ });

		// Имя и занятость лежат в разных строках: строка имени занятость не содержит.
		const title = within(links[0]!).getByText(/\[SU\] Fenicu$/);
		const line = within(links[0]!).getByText('71 · свободен');
		const titleRow = title.parentElement!;
		expect(links[0]).toContainElement(titleRow);
		expect(links[0]).toContainElement(line);
		expect(titleRow).not.toContainElement(line);
		expect(line).not.toContainElement(title);
		expect(links[0]!.getAttribute('title')).toMatch(/\[SU\] Fenicu · 71 · свободен$/);

		const busy = within(links[1]!).getByText(/^54 · учёба до \d{2}\.\d{2} \d{2}:\d{2}$/);
		const busyTitleRow = within(links[1]!).getByText(/\[LA\] Iko$/).parentElement!;
		expect(busyTitleRow).not.toContainElement(busy);
		expect(links[1]!.getAttribute('title')).toMatch(/\[LA\] Iko · 54 · учёба до /);
		// Значок «Telegram не в сети» остался в строке имени.
		expect(busyTitleRow).toContainElement(within(links[1]!).getByTitle('Telegram не в сети'));
		expect(within(links[0]!).queryByTitle('Telegram не в сети')).toBeNull();

		const deleting = within(column()).getAllByRole('listitem')[2]!;
		expect(within(deleting).queryByRole('link')).toBeNull();
		const full = deleting.querySelector('[title*="Khalida"]');
		expect(full?.getAttribute('title')).toMatch(/\[SU\] Khalida · .*удаляется/);
	});

	it('открытый аккаунт выделен; другой открывается на том же разделе', () => {
		accounts.list = [account(1), account(2)];
		open('/a/1/journal', { account: '1' });
		const links = within(column()).getAllByRole('link', { name: /acc\d/ });
		expect(links.map((a) => a.getAttribute('href'))).toEqual(['/a/1/journal', '/a/2/journal']);
		expect(links[0]).toHaveAttribute('aria-current', 'true');
		expect(links[1]).not.toHaveAttribute('aria-current');
	});

	it('из настроек другой аккаунт открывается на той же группе настроек', () => {
		accounts.list = [account(1), account(2)];
		open('/a/1/settings?tab=battle', { account: '1' });
		const links = within(column()).getAllByRole('link', { name: /acc\d/ });
		expect(links.map((a) => a.getAttribute('href'))).toEqual(['/a/1/settings?tab=battle', '/a/2/settings?tab=battle']);
	});

	it('с общего экрана — главная аккаунта', () => {
		accounts.list = [account(1), account(2)];
		open('/password');
		const links = within(column()).getAllByRole('link', { name: /acc\d/ });
		expect(links.map((a) => a.getAttribute('href'))).toEqual(['/a/1', '/a/2']);
	});

	it('удаляемый аккаунт — без ссылки', () => {
		accounts.list = [account(1), account(2, { status: 'deleting' })];
		open('/a/1', { account: '1' });
		const rows = within(column()).getAllByRole('listitem');
		expect(rows[1]).toHaveTextContent('acc2');
		expect(rows[1]).toHaveTextContent('удаляется');
		expect(within(rows[1]!).queryByRole('link')).toBeNull();
	});

	it('Telegram не в сети — значок у имени', () => {
		accounts.list = [account(1), account(2, { tg: { user_id: 2, online: false } })];
		open('/a/1', { account: '1' });
		const rows = within(column()).getAllByRole('listitem');
		expect(within(rows[0]!).queryByTitle('Telegram не в сети')).toBeNull();
		expect(within(rows[1]!).getByTitle('Telegram не в сети')).toBeInTheDocument();
	});

	it('«+» открывает создание аккаунта, «управление» ведёт на /accounts', async () => {
		accounts.list = [account(1)];
		open('/a/1', { account: '1' });
		expect(within(column()).getByRole('link', { name: 'управление' })).toHaveAttribute('href', '/accounts');
		await userEvent.setup().click(within(column()).getByRole('button', { name: 'Добавить аккаунт' }));
		const dialog = screen.getByRole('dialog', { name: 'Новый аккаунт' });
		expect(within(dialog).getByLabelText('Имя нового аккаунта')).toBeInTheDocument();
		expect(within(dialog).getByRole('button', { name: 'Создать' })).toBeDisabled();
	});

	it('сворачивается до точек с буквой, состояние — в localStorage', async () => {
		const user = userEvent.setup();
		accounts.list = [account(1, { name: '☣️ [LA] iko' }), account(2)];
		open('/a/1', { account: '1' });
		await user.click(within(column()).getByRole('button', { name: 'Свернуть список аккаунтов' }));
		expect(localStorage.getItem('pyrobot.accountsCollapsed')).toBe('1');
		const links = within(column()).getAllByRole('link', { name: /iko|acc2/ });
		expect(links.map((a) => a.textContent?.trim())).toEqual(['I', 'A']);
		cleanup();

		open('/a/1', { account: '1' });
		const expand = within(column()).getByRole('button', { name: 'Развернуть список аккаунтов' });
		expect(expand).toHaveAttribute('aria-expanded', 'false');
		await user.click(expand);
		expect(localStorage.getItem('pyrobot.accountsCollapsed')).toBe('0');
		expect(within(column()).getByRole('link', { name: 'управление' })).toBeInTheDocument();
	});
});

describe('полоса разделов', () => {
	it('иконки разделов аккаунта с подписью, активный отмечен', () => {
		accounts.list = [account(1)];
		open('/a/1/settings', { account: '1' });
		const settings = within(rail()).getByRole('link', { name: 'Настройки' });
		expect(settings).toHaveAttribute('href', '/a/1/settings');
		expect(settings).toHaveAttribute('title', 'Настройки');
		expect(settings).toHaveAttribute('aria-current', 'page');
		expect(within(rail()).getByRole('link', { name: 'Главная' })).not.toHaveAttribute('aria-current');
	});

	it('«Сервер» — только владельцу', () => {
		accounts.list = [account(1)];
		open('/a/1', { account: '1' });
		expect(within(rail()).queryByRole('link', { name: 'Сервер' })).toBeNull();
		expect(within(rail()).getByRole('link', { name: 'Пароль и коды' })).toHaveAttribute('href', '/password');
		cleanup();

		session.role = 'owner';
		open('/a/1', { account: '1' });
		expect(within(rail()).getByRole('link', { name: 'Сервер' })).toHaveAttribute('href', '/admin');
	});

	it('непрочитанные — значок у «Уведомлений»', () => {
		accounts.list = [account(1)];
		current.ctx = {
			id: 1,
			live: { status: 'open', retryIn: 0 },
			unread: { count: 3 },
			engine: { status: null }
		} as unknown as typeof current.ctx;
		open('/a/1', { account: '1' });
		const link = within(rail()).getByRole('link', { name: 'Уведомления' });
		expect(link).toHaveAccessibleDescription('непрочитанных предупреждений и ошибок: 3');
		expect(link).toHaveTextContent('3');
	});

	it('выход — только после подтверждения', async () => {
		const confirm = vi.spyOn(dialogs, 'confirm').mockResolvedValue(false);
		accounts.list = [account(1)];
		open('/a/1', { account: '1' });
		await userEvent.setup().click(within(rail()).getByRole('button', { name: 'Выйти (admin)' }));
		expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Выйти из админки?' }));
		expect(session.signOut).not.toHaveBeenCalled();

		confirm.mockResolvedValue(true);
		await userEvent.setup().click(within(rail()).getByRole('button', { name: 'Выйти (admin)' }));
		expect(session.signOut).toHaveBeenCalledOnce();
	});

	it('тема — одна кнопка по кругу: тёмная → светлая → как в системе', async () => {
		const user = userEvent.setup();
		theme.set('dark');
		open('/accounts');
		await user.click(within(rail()).getByRole('button', { name: 'Тема: тёмная' }));
		expect(theme.pref).toBe('light');
		await user.click(within(rail()).getByRole('button', { name: 'Тема: светлая' }));
		expect(theme.pref).toBe('system');
		await user.click(within(rail()).getByRole('button', { name: 'Тема: как в системе' }));
		expect(theme.pref).toBe('dark');
	});
});

describe('шапка страницы', () => {
	const banners = createRawSnippet(() => ({ render: () => '<p>плашка</p>' }));
	const frame = (over: Partial<AccountFrame> = {}): AccountFrame => ({
		title: '☣️[LA] Iko',
		engine: null,
		engineError: null,
		state: {},
		live: 'open',
		retryIn: 0,
		stopped: false,
		banners,
		...over
	});
	const body = createRawSnippet(() => ({ render: () => '<p>тело</p>' }));

	it('общая страница — только раздел', () => {
		render(Page, { title: 'Пароль и коды', children: body });
		expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent(/^Пароль и коды$/);
		expect(screen.queryByTitle(/связь/)).toBeNull();
	});

	it('экран аккаунта — «аккаунт · раздел», точка связи и плашки под шапкой', () => {
		render(Page, { props: { title: 'Журнал', children: body }, context: accountFrameContext(frame()) });
		const h1 = screen.getByRole('heading', { level: 1 });
		expect(h1).toHaveTextContent('☣️[LA] Iko · Журнал');
		expect(screen.getByTitle('связь есть')).toBeInTheDocument();
		const banner = screen.getByText('плашка');
		const text = screen.getByText('тело');
		expect(h1.compareDocumentPosition(banner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(banner.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
	});

	it('любой раздел аккаунта — тот же статус движка, что на главной: LIVE, Telegram, след. решение', () => {
		const engine = fixture<EngineStatus>('engine_status');
		render(Page, { props: { title: 'Журнал', children: body }, context: accountFrameContext(frame({ engine })) });
		const status = screen.getByRole('region', { name: 'Статус' });
		expect(status.closest('header')).not.toBeNull();
		expect(status).toHaveTextContent('LIVE');
		expect(status).toHaveTextContent('TG: online');
		expect(status).toHaveTextContent('след. решение');
	});

	it('общая страница — без статуса движка', () => {
		render(Page, { title: 'Сервер', children: body });
		expect(screen.queryByRole('region', { name: 'Статус' })).toBeNull();
	});

	it('движок не запущен — приглушённое «движок не запущен» вместо «нет связи»', () => {
		render(Page, {
			props: { title: 'Главная', children: body },
			context: accountFrameContext(frame({ live: 'offline', retryIn: 4000, stopped: true }))
		});
		const dot = screen.getByTitle('движок не запущен');
		expect(dot.querySelector('[aria-hidden]')).toHaveClass('bg-zinc-500');
		expect(screen.queryByTitle(/нет связи/)).toBeNull();
	});

	it('движок запущен, поток оборван — «нет связи» с повтором', () => {
		render(Page, {
			props: { title: 'Главная', children: body },
			context: accountFrameContext(frame({ live: 'offline', retryIn: 4000 }))
		});
		expect(screen.getByTitle('нет связи · повтор через 4 с')).toBeInTheDocument();
	});
});
