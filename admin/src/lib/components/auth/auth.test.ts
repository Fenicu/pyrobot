import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { createApi } from '$lib/api/client';
import { stopApp as defaultStopApp } from '$lib/app.svelte';
import { AccountsStore } from '$lib/stores/accounts.svelte';
import { Session } from '$lib/stores/session.svelte';
import { json, mockFetch } from '$lib/test/fetch';
import InvitePage from '../../../routes/invite/[token]/+page.svelte';
import PasswordPage from '../../../routes/password/+page.svelte';
import RecoverPage from '../../../routes/recover/+page.svelte';
import RecoveryCodes from './RecoveryCodes.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

afterEach(() => {
	cleanup();
	defaultStopApp();
	vi.mocked(goto).mockClear();
});

describe('RecoveryCodes', () => {
	it('RecoveryCodes: копирование и скачивание .txt', async () => {
		const user = userEvent.setup();
		const writeText = vi.fn(async () => {});
		vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });

		const codes = ['code-1', 'code-2'];
		render(RecoveryCodes, { codes });

		expect(screen.getByText('code-1')).toHaveClass('font-mono');
		expect(screen.getByText('code-2')).toHaveClass('font-mono');

		await user.click(screen.getByRole('button', { name: 'Скопировать' }));
		expect(writeText).toHaveBeenCalledWith('code-1\ncode-2');

		const createObjectURL = vi.fn(() => 'blob:url');
		const revokeObjectURL = vi.fn();
		const origCreate = URL.createObjectURL;
		const origRevoke = URL.revokeObjectURL;
		URL.createObjectURL = createObjectURL;
		URL.revokeObjectURL = revokeObjectURL;
		try {
			await user.click(screen.getByRole('button', { name: 'Скачать .txt' }));
			expect(createObjectURL).toHaveBeenCalled();
		} finally {
			URL.createObjectURL = origCreate;
			URL.revokeObjectURL = origRevoke;
		}
	});
});

describe('аутентификация и восстановление', () => {
	it('приглашение: 410 — понятный текст, форма не показывается', async () => {
		const fetch = mockFetch(() => json({ detail: 'invite_gone' }, 410));
		render(InvitePage, { token: 'token-gone', fetchImpl: fetch });
		expect(
			await screen.findByText('Приглашение уже использовано, отозвано или истекло')
		).toBeInTheDocument();
		expect(screen.queryByLabelText('Логин')).toBeNull();
		expect(screen.queryByRole('button', { name: 'Зарегистрироваться' })).toBeNull();
	});

	it('регистрация показывает коды один раз и ведёт на /accounts после отметки', async () => {
		const user = userEvent.setup();
		const session = new Session();
		const fetch = mockFetch((c) => {
			if (c.method === 'GET') return json({ expires_at: '2026-10-10T00:00:00Z' });
			if (c.method === 'POST') {
				return json(
					{
						login: 'alice',
						csrf_token: 'csrf-1',
						role: 'user',
						recovery_codes: ['412-K7QM2-XH9TD', '413-AB345-KL890']
					},
					201
				);
			}
			return json({}, 404);
		});

		render(InvitePage, { token: 'valid-token', session, fetchImpl: fetch });
		await screen.findByLabelText('Логин');
		await user.type(screen.getByLabelText('Логин'), 'alice');
		await user.type(screen.getByLabelText('Пароль (не короче 12)'), 'password12345');
		await user.type(screen.getByLabelText('Пароль ещё раз'), 'password12345');

		await user.click(screen.getByRole('button', { name: 'Зарегистрироваться' }));

		expect(await screen.findByText('412-K7QM2-XH9TD')).toBeInTheDocument();
		expect(screen.getByText('413-AB345-KL890')).toBeInTheDocument();
		expect(session.status).toBe('authenticated');
		expect(session.role).toBe('user');
		expect(session.login).toBe('alice');

		const continueBtn = screen.getByRole('button', { name: 'Продолжить' });
		expect(continueBtn).toBeDisabled();

		const checkbox = screen.getByLabelText('Коды сохранены');
		await user.click(checkbox);
		expect(continueBtn).toBeEnabled();

		await user.click(continueBtn);
		expect(goto).toHaveBeenCalledWith('/accounts');
	});

	it('несовпадение паролей и короткий пароль — без запроса', async () => {
		const user = userEvent.setup();
		const fetch = mockFetch(() => json({ expires_at: '2026-10-10T00:00:00Z' }));

		render(InvitePage, { token: 'valid-token', fetchImpl: fetch });
		await screen.findByLabelText('Логин');
		await user.type(screen.getByLabelText('Логин'), 'alice');

		await user.type(screen.getByLabelText('Пароль (не короче 12)'), 'short');
		expect(screen.getByText('не короче 12 символов')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Зарегистрироваться' })).toBeDisabled();

		await user.type(screen.getByLabelText('Пароль (не короче 12)'), '1234567');
		await user.type(screen.getByLabelText('Пароль ещё раз'), 'differentpass');
		expect(screen.getByText('пароли не совпадают')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Зарегистрироваться' })).toBeDisabled();

		const posts = fetch.calls.filter((c) => c.method === 'POST');
		expect(posts).toHaveLength(0);
	});

	it('восстановление: шаг кода, выбор вида кода, вход после успеха', async () => {
		const user = userEvent.setup();
		const session = new Session();
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/auth/recover/start') return json({}, 202);
			if (c.url === '/api/v1/auth/recover/finish') {
				const body = JSON.parse(c.body);
				return json({ login: body.login, csrf_token: 'csrf-rec', role: 'user' }, 200);
			}
			return json({}, 404);
		});

		render(RecoverPage, { session, fetchImpl: fetch });

		// Шаг 1: ввод логина
		await user.type(screen.getByLabelText('Логин'), 'bob');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		expect(
			await screen.findByText(/Если у учётки есть аккаунт онлайн в Telegram, код отправлен в «Избранное»/)
		).toBeInTheDocument();

		// Шаг 2: переключатель «Код из Telegram» / «Код восстановления»
		expect(screen.getByLabelText('Код из Telegram')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Код восстановления' }));
		expect(screen.getByLabelText('Код восстановления')).toBeInTheDocument();

		await user.type(screen.getByLabelText('Код восстановления'), '412-K7QM2-XH9TD');
		await user.type(screen.getByLabelText('Новый пароль (не короче 12)'), 'newpassword123');
		await user.type(screen.getByLabelText('Новый пароль ещё раз'), 'newpassword123');

		await user.click(screen.getByRole('button', { name: 'Сменить пароль и войти' }));

		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/'));
		expect(session.status).toBe('authenticated');
		expect(session.login).toBe('bob');
		const finishCall = fetch.calls.find((c) => c.url === '/api/v1/auth/recover/finish')!;
		expect(JSON.parse(finishCall.body)).toEqual({
			login: 'bob',
			recovery_code: '412-K7QM2-XH9TD',
			password: 'newpassword123'
		});
	});

	it('перевыпуск кодов: неверный пароль — текст invalid_password', async () => {
		const user = userEvent.setup();
		let ok = false;
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/auth/recovery-codes') {
				return ok
					? json({ codes: ['new-code-1', 'new-code-2'] }, 200)
					: json({ detail: 'invalid_password' }, 403);
			}
			return json({}, 404);
		});
		const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);

		render(PasswordPage, { api });

		expect(screen.getByText(/прежние.*больше не действуют/i)).toBeInTheDocument();

		await user.type(screen.getByLabelText('Пароль для подтверждения'), 'wrong');
		await user.click(screen.getByRole('button', { name: 'Получить новые коды' }));

		expect(await screen.findByRole('alert')).toHaveTextContent('Неверный пароль');

		ok = true;
		await user.type(screen.getByLabelText('Пароль для подтверждения'), 'right');
		await user.click(screen.getByRole('button', { name: 'Получить новые коды' }));

		expect(await screen.findByText('new-code-1')).toBeInTheDocument();
		expect(screen.getByText('new-code-2')).toBeInTheDocument();
	});

	it('приглашение: невалидный логин — кнопка заблокирована, ноль POST, русская подсказка', async () => {
		const user = userEvent.setup();
		const fetch = mockFetch(() => json({ expires_at: '2026-10-10T00:00:00Z' }));

		render(InvitePage, { token: 'valid-token', fetchImpl: fetch });
		await screen.findByLabelText('Логин');

		const loginInput = screen.getByLabelText('Логин');
		const passwordInput = screen.getByLabelText('Пароль (не короче 12)');
		const repeatInput = screen.getByLabelText('Пароль ещё раз');
		const submitBtn = screen.getByRole('button', { name: 'Зарегистрироваться' });

		await user.type(passwordInput, 'validpassword123');
		await user.type(repeatInput, 'validpassword123');

		// Невалидный логин: кириллица («Вася»)
		await user.type(loginInput, 'Вася');
		expect(
			screen.getByText('латиница, цифры, точка, дефис, подчёркивание; 3–64 символа')
		).toBeInTheDocument();
		expect(submitBtn).toBeDisabled();

		// Клик по заблокированной кнопке не отправляет POST
		await user.click(submitBtn);
		const posts = fetch.calls.filter((c) => c.method === 'POST');
		expect(posts).toHaveLength(0);
	});

	it('восстановление: вызывает stopApp() перед adopt()', async () => {
		const user = userEvent.setup();
		const session = new Session();
		const stopApp = vi.fn();
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/auth/recover/start') return json({}, 202);
			if (c.url === '/api/v1/auth/recover/finish') {
				return json({ login: 'alice', csrf_token: 'csrf-rec', role: 'user' }, 200);
			}
			return json({}, 404);
		});

		render(RecoverPage, { session, stopApp, fetchImpl: fetch });

		await user.type(screen.getByLabelText('Логин'), 'alice');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		await screen.findByRole('button', { name: 'Код восстановления' });
		await user.click(screen.getByRole('button', { name: 'Код восстановления' }));

		await user.type(screen.getByLabelText('Код восстановления'), '412-K7QM2-XH9TD');
		await user.type(screen.getByLabelText('Новый пароль (не короче 12)'), 'newpassword123');
		await user.type(screen.getByLabelText('Новый пароль ещё раз'), 'newpassword123');

		await user.click(screen.getByRole('button', { name: 'Сменить пароль и войти' }));

		await vi.waitFor(() => expect(stopApp).toHaveBeenCalledTimes(1));
		expect(session.status).toBe('authenticated');
		expect(session.login).toBe('alice');
	});

	it('восстановление: для вошедшего пользователя перезапрашивает список аккаунтов (stopApp -> adopt -> startApp)', async () => {
		const user = userEvent.setup();
		const session = new Session();
		session.adopt({ login: 'old-user', csrf_token: 'csrf-old', role: 'user' });

		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/auth/recover/start') return json({}, 202);
			if (c.url === '/api/v1/auth/recover/finish') {
				return json({ login: 'alice', csrf_token: 'csrf-alice', role: 'user' }, 200);
			}
			if (c.url === '/api/v1/accounts') {
				return json([{ id: session.login === 'alice' ? 2 : 1, name: `acc-${session.login}` }]);
			}
			return json({}, 404);
		});

		const accounts = new AccountsStore(createApi(session.hooks, fetch));
		accounts.start();
		await vi.waitFor(() => expect(accounts.list).toEqual([{ id: 1, name: 'acc-old-user' }]));

		let stoppedBeforeAdopt = false;
		let startedAfterAdopt = false;
		const calls: string[] = [];
		const stopApp = vi.fn(() => {
			stoppedBeforeAdopt = session.login === 'old-user';
			calls.push('stop');
			accounts.stop();
		});
		const startApp = vi.fn(() => {
			startedAfterAdopt = session.login === 'alice';
			calls.push('start');
			accounts.start();
		});

		render(RecoverPage, { session, stopApp, startApp, fetchImpl: fetch });

		await user.type(screen.getByLabelText('Логин'), 'alice');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		await screen.findByRole('button', { name: 'Код восстановления' });
		await user.click(screen.getByRole('button', { name: 'Код восстановления' }));

		await user.type(screen.getByLabelText('Код восстановления'), '412-K7QM2-XH9TD');
		await user.type(screen.getByLabelText('Новый пароль (не короче 12)'), 'newpassword123');
		await user.type(screen.getByLabelText('Новый пароль ещё раз'), 'newpassword123');

		await user.click(screen.getByRole('button', { name: 'Сменить пароль и войти' }));

		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/'));
		expect(calls).toEqual(['stop', 'start']);
		expect(stoppedBeforeAdopt).toBe(true);
		expect(startedAfterAdopt).toBe(true);
		expect(session.status).toBe('authenticated');
		expect(session.login).toBe('alice');
		await vi.waitFor(() => expect(accounts.list).toEqual([{ id: 2, name: 'acc-alice' }]));
		const accountCalls = fetch.calls.filter((c) => c.url === '/api/v1/accounts');
		expect(accountCalls.length).toBe(2);
	});

	it('приглашение: для вошедшего пользователя перезапрашивает список аккаунтов (stopApp -> adopt -> startApp)', async () => {
		const user = userEvent.setup();
		const session = new Session();
		session.adopt({ login: 'old-user', csrf_token: 'csrf-old', role: 'user' });

		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/invites/valid-token') {
				return json({ expires_at: '2026-10-10T00:00:00Z' });
			}
			if (c.url === '/api/v1/invites/valid-token/accept') {
				return json(
					{
						login: 'bob',
						csrf_token: 'csrf-bob',
						role: 'user',
						recovery_codes: ['412-K7QM2-XH9TD']
					},
					201
				);
			}
			if (c.url === '/api/v1/accounts') {
				return json([{ id: session.login === 'bob' ? 2 : 1, name: `acc-${session.login}` }]);
			}
			return json({}, 404);
		});

		const accounts = new AccountsStore(createApi(session.hooks, fetch));
		accounts.start();
		await vi.waitFor(() => expect(accounts.list).toEqual([{ id: 1, name: 'acc-old-user' }]));

		let stoppedBeforeAdopt = false;
		let startedAfterAdopt = false;
		const calls: string[] = [];
		const stopApp = vi.fn(() => {
			stoppedBeforeAdopt = session.login === 'old-user';
			calls.push('stop');
			accounts.stop();
		});
		const startApp = vi.fn(() => {
			startedAfterAdopt = session.login === 'bob';
			calls.push('start');
			accounts.start();
		});

		render(InvitePage, { token: 'valid-token', session, stopApp, startApp, fetchImpl: fetch });
		await screen.findByLabelText('Логин');

		await user.type(screen.getByLabelText('Логин'), 'bob');
		await user.type(screen.getByLabelText('Пароль (не короче 12)'), 'password12345');
		await user.type(screen.getByLabelText('Пароль ещё раз'), 'password12345');

		await user.click(screen.getByRole('button', { name: 'Зарегистрироваться' }));

		expect(await screen.findByText('412-K7QM2-XH9TD')).toBeInTheDocument();
		expect(calls).toEqual(['stop', 'start']);
		expect(stoppedBeforeAdopt).toBe(true);
		expect(startedAfterAdopt).toBe(true);
		expect(session.status).toBe('authenticated');
		expect(session.login).toBe('bob');
		await vi.waitFor(() => expect(accounts.list).toEqual([{ id: 2, name: 'acc-bob' }]));
		const accountCalls = fetch.calls.filter((c) => c.url === '/api/v1/accounts');
		expect(accountCalls.length).toBe(2);
	});

	it('восстановление: для анонимного пользователя запускает опрос аккаунтов после успешного входа', async () => {
		const user = userEvent.setup();
		const session = new Session();

		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/auth/recover/start') return json({}, 202);
			if (c.url === '/api/v1/auth/recover/finish') {
				return json({ login: 'alice', csrf_token: 'csrf-alice', role: 'user' }, 200);
			}
			if (c.url === '/api/v1/accounts') {
				return json([{ id: 1, name: `acc-${session.login}` }]);
			}
			return json({}, 404);
		});

		const accounts = new AccountsStore(createApi(session.hooks, fetch));
		expect(accounts.list).toBeNull();

		const calls: string[] = [];
		const stopApp = vi.fn(() => {
			calls.push('stop');
			accounts.stop();
		});
		const startApp = vi.fn(() => {
			calls.push('start');
			accounts.start();
		});

		render(RecoverPage, { session, stopApp, startApp, fetchImpl: fetch });

		await user.type(screen.getByLabelText('Логин'), 'alice');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		await screen.findByRole('button', { name: 'Код восстановления' });
		await user.click(screen.getByRole('button', { name: 'Код восстановления' }));

		await user.type(screen.getByLabelText('Код восстановления'), '412-K7QM2-XH9TD');
		await user.type(screen.getByLabelText('Новый пароль (не короче 12)'), 'newpassword123');
		await user.type(screen.getByLabelText('Новый пароль ещё раз'), 'newpassword123');

		await user.click(screen.getByRole('button', { name: 'Сменить пароль и войти' }));

		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/'));
		expect(calls).toEqual(['stop', 'start']);
		expect(session.status).toBe('authenticated');
		expect(session.login).toBe('alice');
		await vi.waitFor(() => expect(accounts.list).toEqual([{ id: 1, name: 'acc-alice' }]));
		const accountCalls = fetch.calls.filter((c) => c.url === '/api/v1/accounts');
		expect(accountCalls.length).toBe(1);
	});

	it('приглашение: для анонимного пользователя запускает опрос аккаунтов после регистрации', async () => {
		const user = userEvent.setup();
		const session = new Session();

		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/invites/valid-token') {
				return json({ expires_at: '2026-10-10T00:00:00Z' });
			}
			if (c.url === '/api/v1/invites/valid-token/accept') {
				return json(
					{
						login: 'alice',
						csrf_token: 'csrf-alice',
						role: 'user',
						recovery_codes: ['412-K7QM2-XH9TD']
					},
					201
				);
			}
			if (c.url === '/api/v1/accounts') {
				return json([{ id: 1, name: `acc-${session.login}` }]);
			}
			return json({}, 404);
		});

		const accounts = new AccountsStore(createApi(session.hooks, fetch));
		expect(accounts.list).toBeNull();

		const calls: string[] = [];
		const stopApp = vi.fn(() => {
			calls.push('stop');
			accounts.stop();
		});
		const startApp = vi.fn(() => {
			calls.push('start');
			accounts.start();
		});

		render(InvitePage, { token: 'valid-token', session, stopApp, startApp, fetchImpl: fetch });
		await screen.findByLabelText('Логин');

		await user.type(screen.getByLabelText('Логин'), 'alice');
		await user.type(screen.getByLabelText('Пароль (не короче 12)'), 'password12345');
		await user.type(screen.getByLabelText('Пароль ещё раз'), 'password12345');

		await user.click(screen.getByRole('button', { name: 'Зарегистрироваться' }));

		expect(await screen.findByText('412-K7QM2-XH9TD')).toBeInTheDocument();
		expect(calls).toEqual(['stop', 'start']);
		expect(session.status).toBe('authenticated');
		expect(session.login).toBe('alice');
		await vi.waitFor(() => expect(accounts.list).toEqual([{ id: 1, name: 'acc-alice' }]));
		const accountCalls = fetch.calls.filter((c) => c.url === '/api/v1/accounts');
		expect(accountCalls.length).toBe(1);
	});
});

