import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { AccountOrderStore } from '$lib/accounts/order.svelte';
import { createApi } from '$lib/api/client';
import type { AccountOut, EngineStatus } from '$lib/api/types';
import { AccountsStore } from '$lib/stores/accounts.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { fmtTime } from '$lib/util/format';
import EngineDownBanner from '../EngineDownBanner.svelte';
import AccountsView from './AccountsView.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };

const account = (id: number, name: string, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: 100 + id, online: true },
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

const LIST = [
	account(1, 'main', {
		tg: { user_id: 267519921, online: true },
		last_action_at: '2026-09-27T19:05:00Z',
		unread: { warn: 2, error: 1 }
	}),
	account(2, 'twink', {
		status: 'disabled',
		mode: 'dry_run',
		paused: true,
		killed: true,
		tg: { user_id: null, online: false }
	}),
	account(3, 'broken', {
		status: 'error',
		status_reason: 'crash_loop:ValueError',
		tg: { user_id: 103, online: false }
	}),
	account(4, 'old', { status: 'deleting' })
];

async function setup(list: AccountOut[], handler: (c: Call) => Response | undefined = () => undefined) {
	const state = { list };
	const fetch = mockFetch(
		(c) =>
			handler(c) ?? (c.method === 'GET' && c.url === '/api/v1/accounts' ? json(state.list) : json({ detail: 'x' }, 500))
	);
	const api = createApi(hooks, fetch);
	const store = new AccountsStore(api);
	await store.load();
	render(AccountsView, { api, store });
	return { fetch, store, state, user: userEvent.setup() };
}

const card = (name: string) =>
	screen.getAllByRole('listitem').find((r) => within(r).queryByText(name, { exact: true }))!;
const names = (c: Call[]) => c.map((x) => `${x.method} ${x.url}`);

/** Действие из меню «⋯» карточки. */
async function act(user: ReturnType<typeof userEvent.setup>, name: string, action: string) {
	await user.click(within(card(name)).getByRole('button', { name: 'Действия' }));
	await user.click(within(card(name)).getByRole('button', { name: action }));
}

async function openMenu(user: ReturnType<typeof userEvent.setup>, name: string) {
	await user.click(within(card(name)).getByRole('button', { name: 'Действия' }));
	return within(card(name));
}

async function startCreate(user: ReturnType<typeof userEvent.setup>) {
	await user.click(screen.getByRole('button', { name: 'Добавить' }));
	return within(screen.getByRole('dialog', { name: 'Новый аккаунт' }));
}

afterEach(() => {
	cleanup();
	vi.mocked(goto).mockClear();
	vi.useRealTimers();
});

describe('экран аккаунтов', () => {
	it('карточки: точка, титул, уровень, до скольки и чем занят; ведут на главную аккаунта', async () => {
		const until = new Date(Date.now() + 3_600_000).toISOString();
		await setup([
			account(1, 'main', { level: 54, busy: { activity: 'learn', until } }),
			account(2, 'twink', { status: 'disabled' }),
			account(3, 'alt', { tg: { user_id: 103, online: false } })
		]);
		expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Аккаунты');
		const main = within(card('main'));
		expect(main.getByRole('link', { name: 'main' })).toHaveAttribute('href', '/a/1');
		expect(main.getByRole('link', { name: 'main' })).toHaveAccessibleDescription('учёба');
		expect(main.getByRole('img')).toHaveAccessibleName('работает');
		expect(main.getByText('ур. 54')).toBeInTheDocument();
		expect(main.getByText(fmtTime(until))).toBeInTheDocument();

		const twink = within(card('twink'));
		expect(twink.getByRole('img')).toHaveAccessibleName('выключен');
		expect(twink.getByText('выключен', { selector: 'p' })).toBeInTheDocument();
		expect(twink.queryByText(/^ур\./)).toBeNull();
		expect(within(card('alt')).getByTitle('Telegram не в сети')).toBeInTheDocument();
	});

	it('проблема — текст предупреждения или причины и красная рамка', async () => {
		await setup([
			account(1, 'main', { unread: { warn: 2, error: 1 }, alert: { level: 'error', text: 'Не хватает денег' } }),
			account(2, 'warned', { alert: { level: 'warn', text: 'Мало сил' } }),
			account(3, 'broken', { status: 'error', status_reason: 'crash_loop:ValueError' }),
			account(4, 'calm')
		]);
		expect(within(card('main')).getByText('Не хватает денег')).toHaveClass('text-bad-fg');
		expect(within(card('main')).getByRole('img')).toHaveAccessibleName('ошибка');
		expect(card('main').firstElementChild).toHaveClass('border-bad/60');
		expect(within(card('warned')).getByText('Мало сил')).toHaveClass('text-warn-fg');
		expect(card('warned').firstElementChild).not.toHaveClass('border-bad/60');
		expect(within(card('broken')).getByText('движок несколько раз упал подряд: ValueError')).toBeInTheDocument();
		expect(card('broken').firstElementChild).toHaveClass('border-bad/60');
		expect(card('calm').firstElementChild).not.toHaveClass('border-bad/60');
	});

	it('удаляемый аккаунт — без ссылки и без действий', async () => {
		await setup(LIST);
		const old = within(card('old'));
		expect(old.getByText('удаляется')).toBeInTheDocument();
		expect(old.getByRole('img')).toHaveAccessibleName('удаляется');
		expect(old.queryByRole('button')).toBeNull();
		expect(old.queryByRole('link')).toBeNull();
		// У остальных — ссылка и меню действий.
		expect(within(card('broken')).getByRole('link', { name: 'broken' })).toHaveAttribute('href', '/a/3');
		expect(within(card('broken')).getByRole('button', { name: 'Действия' })).toBeInTheDocument();
	});

	it('меню «⋯»: включение по статусу, переименование, удаление, вход в Telegram', async () => {
		const { user } = await setup(LIST);
		let menu = await openMenu(user, 'main');
		expect(within(card('main')).getByRole('button', { name: 'Действия' })).toHaveAttribute('aria-expanded', 'true');
		expect(menu.getByRole('button', { name: 'Выключить' })).toBeInTheDocument();
		expect(menu.queryByRole('button', { name: 'Включить' })).toBeNull();
		expect(menu.getByRole('button', { name: 'Переименовать' })).toBeInTheDocument();
		expect(menu.getByRole('button', { name: 'Удалить' })).toBeInTheDocument();
		expect(menu.getByRole('link', { name: 'Вход в Telegram' })).toHaveAttribute('href', '/a/1/telegram');

		// Одно меню за раз; Esc закрывает.
		menu = await openMenu(user, 'twink');
		expect(within(card('main')).queryByRole('button', { name: 'Выключить' })).toBeNull();
		expect(menu.getByRole('button', { name: 'Включить' })).toBeInTheDocument();
		await user.keyboard('{Escape}');
		expect(within(card('twink')).queryByRole('button', { name: 'Включить' })).toBeNull();

		menu = await openMenu(user, 'broken');
		expect(menu.getByRole('button', { name: 'Включить' })).toBeInTheDocument();
		// Щелчок мимо меню закрывает его.
		await user.click(screen.getByRole('heading', { level: 1 }));
		expect(within(card('broken')).queryByRole('button', { name: 'Включить' })).toBeNull();
	});

	it('имя в списке — титул с компанией и командой; поля ввода и подтверждения работают с сырым именем', async () => {
		const { user } = await setup([account(1, 'Fenicu', { company: 'bmesa', team_tag: 'SU' })]);
		expect(screen.getByRole('link', { name: '☣️[SU] Fenicu' })).toHaveAttribute('href', '/a/1');
		await act(user, '☣️[SU] Fenicu', 'Переименовать');
		expect(screen.getByLabelText('Новое имя')).toHaveValue('Fenicu');
		await user.click(screen.getByRole('button', { name: 'Отмена' }));
		await act(user, '☣️[SU] Fenicu', 'Удалить');
		expect(screen.getByLabelText('Имя аккаунта для подтверждения')).toHaveAttribute('placeholder', 'Fenicu');
	});

	it('удаляемый аккаунт перечитывается, пока чистка не закончится', async () => {
		vi.useFakeTimers();
		const { fetch, state } = await setup([account(1, 'main'), account(4, 'old', { status: 'deleting' })]);
		const loads = () => fetch.calls.filter((c) => c.url === '/api/v1/accounts').length;
		expect(loads()).toBe(1);
		await vi.advanceTimersByTimeAsync(3000);
		expect(loads()).toBe(2);
		state.list = [account(1, 'main')];
		await vi.advanceTimersByTimeAsync(3000);
		expect(loads()).toBe(3);
		await waitFor(() => expect(screen.queryByText('old')).toBeNull());
		// Удаляемых больше нет — опрос прекращён.
		await vi.advanceTimersByTimeAsync(10_000);
		expect(loads()).toBe(3);
	});

	it('пустой список — подсказка и «Добавить»', async () => {
		await setup([]);
		expect(screen.getByText('Аккаунтов нет — создайте первый.')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Добавить' })).toBeInTheDocument();
	});

	it('создание ведёт на экран Telegram нового аккаунта', async () => {
		const { fetch, state, user } = await setup([account(1, 'main')], (c) => {
			if (c.method !== 'POST') return undefined;
			state.list = [...state.list, account(5, 'newbie', { tg: { user_id: null, online: false } })];
			return json(state.list[1], 201);
		});
		const dialog = await startCreate(user);
		await user.type(dialog.getByLabelText('Имя нового аккаунта'), '  newbie ');
		await user.click(dialog.getByRole('button', { name: 'Создать' }));
		await waitFor(() => expect(goto).toHaveBeenCalledWith('/a/5/telegram'));
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'POST')!.body)).toEqual({ name: 'newbie' });
		// Список перечитан до перехода: макет аккаунта открывает только аккаунт из списка.
		expect(names(fetch.calls)).toEqual(['GET /api/v1/accounts', 'POST /api/v1/accounts', 'GET /api/v1/accounts']);
		expect(screen.queryByRole('dialog')).toBeNull();
	});

	it('capacity_reached и name_taken — текст у формы, перехода нет', async () => {
		let detail = 'capacity_reached';
		const { user } = await setup([account(1, 'main')], (c) =>
			c.method === 'POST' ? json({ detail }, 409) : undefined
		);
		const dialog = await startCreate(user);
		await user.type(dialog.getByLabelText('Имя нового аккаунта'), 'newbie');
		await user.click(dialog.getByRole('button', { name: 'Создать' }));
		expect(await dialog.findByRole('alert')).toHaveTextContent('Достигнут предел включённых аккаунтов');

		detail = 'name_taken';
		await user.click(dialog.getByRole('button', { name: 'Создать' }));
		await waitFor(() => expect(dialog.getByRole('alert')).toHaveTextContent('Аккаунт с таким именем уже есть'));
		expect(goto).not.toHaveBeenCalled();
		// Имя осталось в поле: исправить и повторить.
		expect(dialog.getByLabelText('Имя нового аккаунта')).toHaveValue('newbie');
	});

	it('limit_reached и server_full — текст у формы', async () => {
		let detail = 'limit_reached';
		const { user } = await setup([account(1, 'main')], (c) =>
			c.method === 'POST' ? json({ detail }, 409) : undefined
		);
		const dialog = await startCreate(user);
		await user.type(dialog.getByLabelText('Имя нового аккаунта'), 'newbie');
		await user.click(dialog.getByRole('button', { name: 'Создать' }));
		expect(await dialog.findByRole('alert')).toHaveTextContent('Достигнут лимит аккаунтов');

		detail = 'server_full';
		await user.click(dialog.getByRole('button', { name: 'Создать' }));
		await waitFor(() => expect(dialog.getByRole('alert')).toHaveTextContent('На сервере нет свободных мест для аккаунтов'));
		expect(goto).not.toHaveBeenCalled();
		// Имя осталось в поле: исправить и повторить.
		expect(dialog.getByLabelText('Имя нового аккаунта')).toHaveValue('newbie');
	});

	it('заблокированный аккаунт: причина и нет кнопки «Включить»', async () => {
		const blocked = account(5, 'spam', {
			status: 'disabled',
			status_reason: 'blocked_by_owner',
			blocked: true,
			blocked_reason: 'Спам',
			tg: { user_id: 105, online: false }
		});
		// Разблокированный: статус выключен, причина блокировки устарела.
		const freed = account(6, 'freed', { status: 'disabled', status_reason: 'blocked_by_owner' });
		const { user } = await setup([account(1, 'main'), blocked, freed]);
		expect(within(card('spam')).getByText('Заблокирован владельцем сервера: Спам')).toBeInTheDocument();
		expect(card('spam').firstElementChild).toHaveClass('border-bad/60');
		const spam = await openMenu(user, 'spam');
		expect(spam.queryByRole('button', { name: 'Включить' })).toBeNull();
		// Остальные действия остаются: разблокирует только владелец сервера.
		expect(spam.getByRole('button', { name: 'Переименовать' })).toBeInTheDocument();
		expect(spam.getByRole('button', { name: 'Удалить' })).toBeInTheDocument();

		const free = await openMenu(user, 'freed');
		expect(free.getByRole('button', { name: 'Включить' })).toBeInTheDocument();
		expect(free.queryByText(/Заблокирован/)).toBeNull();
		expect(free.queryByText('blocked_by_owner')).toBeNull();
	});

	it('переименование шлёт новое имя; name_taken — текст в окне', async () => {
		let taken = true;
		const { fetch, state, user } = await setup([account(1, 'main'), account(2, 'twink')], (c) => {
			if (c.method !== 'PATCH') return undefined;
			if (taken) return json({ detail: 'name_taken' }, 409);
			state.list = [state.list[0]!, account(2, 'alt')];
			return json(state.list[1]);
		});
		await act(user, 'twink', 'Переименовать');
		const dialog = screen.getByRole('dialog');
		const input = within(dialog).getByLabelText('Новое имя');
		expect(input).toHaveValue('twink');
		await user.clear(input);
		await user.type(input, 'main');
		await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
		expect(await within(dialog).findByRole('alert')).toHaveTextContent('Аккаунт с таким именем уже есть');

		taken = false;
		await user.clear(input);
		await user.type(input, 'alt');
		await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));
		await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
		expect(fetch.calls.filter((c) => c.method === 'PATCH').map((c) => [c.url, JSON.parse(c.body)])).toEqual([
			['/api/v1/accounts/2', { name: 'main' }],
			['/api/v1/accounts/2', { name: 'alt' }]
		]);
		expect(await screen.findByText('alt')).toBeInTheDocument();
	});

	it('включение и выключение шлют enabled; capacity_reached — текст над списком', async () => {
		let full = false;
		const { fetch, user } = await setup([account(1, 'main'), account(2, 'twink', { status: 'disabled' })], (c) => {
			if (c.method !== 'PATCH') return undefined;
			return full ? json({ detail: 'capacity_reached' }, 409) : json(account(1, 'main'));
		});
		await act(user, 'main', 'Выключить');
		await waitFor(() => expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1));
		full = true;
		await act(user, 'twink', 'Включить');
		expect(await screen.findByRole('alert')).toHaveTextContent('Достигнут предел включённых аккаунтов');
		expect(fetch.calls.filter((c) => c.method === 'PATCH').map((c) => [c.url, JSON.parse(c.body)])).toEqual([
			['/api/v1/accounts/1', { enabled: false }],
			['/api/v1/accounts/2', { enabled: true }]
		]);
	});

	it('удаление требует точного имени и шлёт confirm_name', async () => {
		const { fetch, state, user } = await setup([account(1, 'main'), account(2, 'twink')], (c) => {
			if (c.method !== 'DELETE') return undefined;
			state.list = [state.list[0]!, account(2, 'twink', { status: 'deleting' })];
			return new Response(null, { status: 202 });
		});
		await act(user, 'twink', 'Удалить');
		const dialog = screen.getByRole('dialog');
		// Текст окна — обычный абзац (перенос строк шаблона не рисуется), как есть — только имя.
		const name = within(dialog).getByText('twink', { selector: '.ext-text' });
		expect(name.closest('p')).not.toHaveClass('ext-text');
		expect(name.closest('p')).toHaveTextContent(/^Аккаунт «twink» будет удалён навсегда: движок остановится, сервис выйдет из сессии Telegram, журнал, настройки/);
		const confirm = within(dialog).getByRole('button', { name: 'Удалить навсегда' });
		const input = within(dialog).getByLabelText('Имя аккаунта для подтверждения');
		expect(confirm).toBeDisabled();
		for (const wrong of ['twin', 'Twink', 'twink ', 'main']) {
			await user.clear(input);
			await user.type(input, wrong);
			expect(confirm).toBeDisabled();
		}
		await user.clear(input);
		await user.type(input, 'twink');
		expect(confirm).toBeEnabled();
		await user.click(confirm);

		await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
		const del = fetch.calls.find((c) => c.method === 'DELETE')!;
		expect(del.url).toBe('/api/v1/accounts/2');
		expect(JSON.parse(del.body)).toEqual({ confirm_name: 'twink' });
		// Список перечитан: до конца чистки аккаунт виден как «удаляется».
		expect(await within(card('twink')).findByText('удаляется')).toBeInTheDocument();
	});
});

const down = (over: Partial<EngineStatus> = {}): EngineStatus => ({
	...fixture<EngineStatus>('engine_status'),
	running: false,
	status: 'disabled',
	status_reason: null,
	host_reason: null,
	...over
});

function banner(
	status: EngineStatus,
	handler: (c: Call) => Response = () => json(account(7, 'x')),
	props: { blocked?: boolean; blockedReason?: string | null } = {}
) {
	const fetch = mockFetch(handler);
	const onchange = vi.fn();
	render(EngineDownBanner, { status, accountId: 7, api: createApi(hooks, fetch), onchange, ...props });
	return { fetch, onchange, user: userEvent.setup() };
}

describe('плашка «движок не запущен»', () => {
	it('движок запущен — плашки нет', () => {
		banner(down({ running: true, status: 'enabled' }));
		expect(screen.queryByRole('status')).toBeNull();
	});

	it('выключенный аккаунт: причина и включение', async () => {
		const { fetch, onchange, user } = banner(down());
		expect(screen.getByRole('status')).toHaveTextContent('Движок не запущен: аккаунт выключен');
		await user.click(screen.getByRole('button', { name: 'Включить' }));
		await waitFor(() => expect(onchange).toHaveBeenCalledOnce());
		const patch = fetch.calls[0]!;
		expect([patch.method, patch.url, JSON.parse(patch.body)]).toEqual([
			'PATCH',
			'/api/v1/accounts/7',
			{ enabled: true }
		]);
	});

	it('аккаунт с ошибкой: причина из status_reason, кнопка есть; нет места — текст', async () => {
		const { user, onchange } = banner(
			down({ status: 'error', status_reason: 'crash_loop:ValueError' }),
			() => json({ detail: 'capacity_reached' }, 409)
		);
		expect(screen.getByRole('status')).toHaveTextContent('Движок не запущен: движок несколько раз упал подряд: ValueError');
		await user.click(screen.getByRole('button', { name: 'Включить' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Достигнут предел включённых аккаунтов');
		expect(onchange).not.toHaveBeenCalled();
	});

	it('заблокированный: причина блокировки вместо кнопки «Включить»', () => {
		banner(
			down({ status: 'disabled', status_reason: 'blocked_by_owner' }),
			undefined,
			{ blocked: true, blockedReason: 'Спам' }
		);
		expect(screen.getByRole('status')).toHaveTextContent(
			'Движок не запущен: заблокирован владельцем сервера: Спам'
		);
		expect(screen.queryByRole('button', { name: 'Включить' })).toBeNull();
		cleanup();

		// После разблокировки причина устарела: аккаунт просто выключен, включить можно.
		banner(down({ status: 'disabled', status_reason: 'blocked_by_owner' }));
		expect(screen.getByRole('status')).toHaveTextContent('Движок не запущен: аккаунт выключен');
		expect(screen.getByRole('button', { name: 'Включить' })).toBeInTheDocument();
	});

	it('включён, но не запущен (чужой хост, старт) и удаляемый — без кнопки', () => {
		banner(down({ status: 'enabled', host_reason: 'locked_elsewhere' }));
		expect(screen.getByRole('status')).toHaveTextContent('Движок не запущен: аккаунт занят другим хостом');
		expect(screen.queryByRole('button', { name: 'Включить' })).toBeNull();
		cleanup();

		banner(down({ status: 'enabled' }));
		expect(screen.getByRole('status')).toHaveTextContent('Движок не запущен: запускается');
		cleanup();

		banner(down({ status: 'deleting' }));
		expect(screen.getByRole('status')).toHaveTextContent('Движок не запущен: аккаунт удаляется');
		expect(screen.queryByRole('button', { name: 'Включить' })).toBeNull();
	});
});

describe('экран аккаунтов: порядок', () => {
	async function setupOrdered(saved: number[] | null) {
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/accounts') return json(LIST);
			if (c.url === '/api/v1/me/ui/account-order' && c.method === 'GET')
				return json({ order: saved && { version: 1, ids: saved } });
			if (c.url === '/api/v1/me/ui/account-order' && c.method === 'PUT') return json(null, 204);
			return json({ detail: 'x' }, 500);
		});
		const api = createApi(hooks, fetch);
		const order = new AccountOrderStore(api);
		const store = new AccountsStore(api, undefined, order);
		await Promise.all([store.load(), order.load()]);
		render(AccountsView, { api, store });
		return { fetch, user: userEvent.setup() };
	}
	const order = () => screen.getAllByRole('listitem').map((li) => li.querySelector('[id$="-name-' + li.dataset.menu + '"]')?.textContent);

	it('карточки — в сохранённом порядке; у удаляемого ручки нет', async () => {
		await setupOrdered([3, 1]);
		expect(order()).toEqual(['broken', 'main', 'twink', 'old']);
		expect(within(card('old')).queryByRole('button')).toBeNull();
	});

	it('стрелка на ручке переставляет карточку и сохраняет порядок', async () => {
		const { fetch, user } = await setupOrdered(null);
		within(card('twink')).getByRole('button', { name: 'Перетащить twink' }).focus();
		await user.keyboard('{ArrowUp}');
		expect(order()).toEqual(['twink', 'main', 'broken', 'old']);
		await waitFor(() =>
			expect(fetch.calls.filter((c) => c.method === 'PUT').map((c) => JSON.parse(c.body))).toEqual([
				{ version: 1, ids: [2, 1, 3, 4] }
			])
		);
		expect(within(card('twink')).getByRole('button', { name: 'Перетащить twink' })).toHaveFocus();
	});
});
