import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import TelegramView from './TelegramView.svelte';

const st = (state: string, extra: object = {}) => ({ state, user_id: null, attempt_id: null, error: null, ...extra });

function setup(handler: (c: Call) => Response) {
	const fetch = mockFetch(handler);
	render(TelegramView, {
		api: createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch)
	});
	return fetch;
}

describe('Вход в Telegram', () => {
	it('online с прода — выход', async () => {
		setup(() => json(fixture('tg_status')));
		expect(await screen.findByText('267519921')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Выйти из Telegram' })).toBeInTheDocument();
	});

	it('телефон → код (неверный — тот же шаг) → 2FA → online; поля очищаются', async () => {
		const user = userEvent.setup();
		let codeTries = 0;
		const fetch = setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start') return json(st('awaiting_code', { attempt_id: 'a1' }));
			if (c.url === '/api/v1/accounts/1/tg/login/code')
				return ++codeTries === 1
					? json(st('awaiting_code', { attempt_id: 'a1', error: 'invalid_code' }))
					: json(st('awaiting_password', { attempt_id: 'a1' }));
			return json(st('online', { user_id: 267519921 }));
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+79990000000');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		const code = await screen.findByLabelText('Код из Telegram');
		expect(code).toHaveAttribute('autocomplete', 'one-time-code');
		expect(code).toHaveAttribute('inputmode', 'numeric');
		await user.type(code, '11111');
		await user.click(screen.getByRole('button', { name: 'Отправить код' }));
		expect(await screen.findByText('Неверный код — попробуйте ещё раз')).toBeInTheDocument();
		expect(screen.getByLabelText('Код из Telegram')).toHaveValue('');
		await user.type(screen.getByLabelText('Код из Telegram'), '22222');
		await user.click(screen.getByRole('button', { name: 'Отправить код' }));
		expect(await screen.findByLabelText('Пароль 2FA')).toHaveAttribute('autocomplete', 'off');
		await user.type(screen.getByLabelText('Пароль 2FA'), 'secret');
		await user.click(screen.getByRole('button', { name: 'Войти' }));
		expect(await screen.findByRole('button', { name: 'Выйти из Telegram' })).toBeInTheDocument();
		const bodies = fetch.calls.filter((c) => c.method === 'POST').map((c) => JSON.parse(c.body));
		expect(bodies).toEqual([
			{ phone: '+79990000000' },
			{ attempt_id: 'a1', code: '11111' },
			{ attempt_id: 'a1', code: '22222' },
			{ attempt_id: 'a1', password: 'secret' }
		]);
	});

	it('409 — попытка устарела; 429 — задержка из Retry-After', async () => {
		const user = userEvent.setup();
		let status = st('unauthorized');
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(status);
			if (c.url === '/api/v1/accounts/1/tg/login/start') {
				status = st('awaiting_code', { attempt_id: 'a1' });
				return json(status);
			}
			if (c.url === '/api/v1/accounts/1/tg/login/code') {
				status = st('unauthorized');
				return json({ detail: 'unknown attempt' }, 409);
			}
			return json({ detail: 'flood_wait' }, 429, { 'Retry-After': '31' });
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+7999');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		await user.type(await screen.findByLabelText('Код из Telegram'), '1');
		await user.click(screen.getByRole('button', { name: 'Отправить код' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Попытка входа устарела');
		expect(await screen.findByLabelText('Телефон аккаунта')).toBeInTheDocument();
	});
});
