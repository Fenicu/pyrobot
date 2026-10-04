import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { createApi } from '$lib/api/client';
import type { AccountOut, EngineStatus } from '$lib/api/types';
import { AccountsStore } from '$lib/stores/accounts.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
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

const row = (name: string) => screen.getAllByRole('row').find((r) => within(r).queryByText(name, { exact: true }))!;
const names = (c: Call[]) => c.map((x) => `${x.method} ${x.url}`);

afterEach(() => {
	cleanup();
	vi.mocked(goto).mockClear();
	vi.useRealTimers();
});

describe('экран аккаунтов', () => {
	it('список показывает поля аккаунта', async () => {
		await setup(LIST);
		const main = within(row('main'));
		expect(main.getByRole('link', { name: 'main' })).toHaveAttribute('href', '/a/1');
		expect(main.getByText('267519921')).toBeInTheDocument();
		expect(main.getByText('в сети')).toBeInTheDocument();
		expect(main.getByText('включён')).toBeInTheDocument();
		expect(main.getByText('LIVE')).toBeInTheDocument();
		// Последнее действие — по Москве.
		expect(main.getByText(/22:05/)).toBeInTheDocument();
		expect(main.getByLabelText('предупреждений: 2')).toBeInTheDocument();
		expect(main.getByLabelText('ошибок: 1')).toBeInTheDocument();

		const twink = within(row('twink'));
		expect(twink.getByText('выключен')).toBeInTheDocument();
		expect(twink.getByText('DRY RUN')).toBeInTheDocument();
		expect(twink.getByText('пауза')).toBeInTheDocument();
		expect(twink.getByText('kill')).toBeInTheDocument();
		expect(twink.getByText('не подключён')).toBeInTheDocument();
		expect(twink.getByRole('button', { name: 'Включить' })).toBeInTheDocument();

		const broken = within(row('broken'));
		expect(broken.getByText('ошибка')).toBeInTheDocument();
		expect(broken.getByText(/движок несколько раз упал подряд: ValueError/)).toBeInTheDocument();
		expect(broken.getByText('не в сети')).toBeInTheDocument();
		expect(broken.getByRole('button', { name: 'Включить' })).toBeInTheDocument();

		// Удаляемый: без действий и без ссылки на аккаунт.
		const old = within(row('old'));
		expect(old.getByText('удаляется')).toBeInTheDocument();
		expect(old.queryByRole('button')).toBeNull();
		expect(old.queryByRole('link')).toBeNull();
	});

	it('имя в списке — титул с компанией и командой; поля ввода и подтверждения работают с сырым именем', async () => {
		const { user } = await setup([account(1, 'Fenicu', { company: 'bmesa', team_tag: 'SU' })]);
		expect(screen.getByRole('link', { name: '☣️[SU] Fenicu' })).toHaveAttribute('href', '/a/1');
		await user.click(screen.getByRole('button', { name: 'Переименовать' }));
		expect(screen.getByLabelText('Новое имя')).toHaveValue('Fenicu');
		await user.click(screen.getByRole('button', { name: 'Отмена' }));
		await user.click(screen.getByRole('button', { name: 'Удалить' }));
		expect(screen.getByLabelText('Имя аккаунта для подтверждения')).toHaveAttribute('placeholder', 'Fenicu');
	});

	it('колонки таблицы одни для заголовка и всех строк; у колонки кнопок — заголовок для чтеца', async () => {
		await setup(LIST);
		const actions = screen.getByRole('columnheader', { name: 'Действия' });
		expect(actions).toHaveClass('sr-only');
		expect(screen.getAllByRole('columnheader')).toHaveLength(7);
		// Последняя дорожка — фиксированной ширины: у строки «удаляется» кнопок нет, а колонки не сдвигаются.
		const grids = screen.getAllByRole('row').map((r) => /md:grid-cols-\[[^\s]+\]/.exec(r.className)?.[0]);
		expect(new Set(grids).size).toBe(1);
		expect(grids[0]).toMatch(/_\d+(\.\d+)?rem\]$/);
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

	it('создание ведёт на экран Telegram нового аккаунта', async () => {
		const { fetch, state, user } = await setup([account(1, 'main')], (c) => {
			if (c.method !== 'POST') return undefined;
			state.list = [...state.list, account(5, 'newbie', { tg: { user_id: null, online: false } })];
			return json(state.list[1], 201);
		});
		await user.type(screen.getByLabelText('Имя нового аккаунта'), '  newbie ');
		await user.click(screen.getByRole('button', { name: 'Создать' }));
		await waitFor(() => expect(goto).toHaveBeenCalledWith('/a/5/telegram'));
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'POST')!.body)).toEqual({ name: 'newbie' });
		// Список перечитан до перехода: макет аккаунта открывает только аккаунт из списка.
		expect(names(fetch.calls)).toEqual(['GET /api/v1/accounts', 'POST /api/v1/accounts', 'GET /api/v1/accounts']);
	});

	it('capacity_reached и name_taken — текст у формы, перехода нет', async () => {
		let detail = 'capacity_reached';
		const { user } = await setup([account(1, 'main')], (c) =>
			c.method === 'POST' ? json({ detail }, 409) : undefined
		);
		await user.type(screen.getByLabelText('Имя нового аккаунта'), 'newbie');
		await user.click(screen.getByRole('button', { name: 'Создать' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Достигнут предел включённых аккаунтов');

		detail = 'name_taken';
		await user.click(screen.getByRole('button', { name: 'Создать' }));
		await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Аккаунт с таким именем уже есть'));
		expect(goto).not.toHaveBeenCalled();
		// Имя осталось в поле: исправить и повторить.
		expect(screen.getByLabelText('Имя нового аккаунта')).toHaveValue('newbie');
	});

	it('limit_reached и server_full — текст у формы', async () => {
		let detail = 'limit_reached';
		const { user } = await setup([account(1, 'main')], (c) =>
			c.method === 'POST' ? json({ detail }, 409) : undefined
		);
		await user.type(screen.getByLabelText('Имя нового аккаунта'), 'newbie');
		await user.click(screen.getByRole('button', { name: 'Создать' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Достигнут лимит аккаунтов');

		detail = 'server_full';
		await user.click(screen.getByRole('button', { name: 'Создать' }));
		await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('На сервере нет свободных мест для аккаунтов'));
		expect(goto).not.toHaveBeenCalled();
		// Имя осталось в поле: исправить и повторить.
		expect(screen.getByLabelText('Имя нового аккаунта')).toHaveValue('newbie');
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
		await setup([account(1, 'main'), blocked, freed]);
		const spam = within(row('spam'));
		expect(spam.getByText('Заблокирован владельцем сервера: Спам')).toBeInTheDocument();
		expect(spam.queryByRole('button', { name: 'Включить' })).toBeNull();
		// Остальные действия остаются: разблокирует только владелец сервера.
		expect(spam.getByRole('button', { name: 'Переименовать' })).toBeInTheDocument();
		expect(spam.getByRole('button', { name: 'Удалить' })).toBeInTheDocument();

		const free = within(row('freed'));
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
		await user.click(within(row('twink')).getByRole('button', { name: 'Переименовать' }));
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
		await user.click(within(row('main')).getByRole('button', { name: 'Выключить' }));
		await waitFor(() => expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(1));
		full = true;
		await user.click(within(row('twink')).getByRole('button', { name: 'Включить' }));
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
		await user.click(within(row('twink')).getByRole('button', { name: 'Удалить' }));
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
		expect(await within(row('twink')).findByText('удаляется')).toBeInTheDocument();
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
