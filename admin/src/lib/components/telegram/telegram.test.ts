import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { EngineStatus } from '$lib/api/types';
import { EngineStore } from '$lib/stores/engine.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import TelegramView from './TelegramView.svelte';

const st = (state: string, extra: object = {}) => ({
	state,
	user_id: null,
	attempt_id: null,
	error: null,
	bound_user_id: null,
	...extra
});

function setup(handler: (c: Call) => Response) {
	const fetch = mockFetch(handler);
	render(TelegramView, {
		api: createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch),
		engine: { status: null }
	});
	return fetch;
}

describe('Вход в Telegram', () => {
	it('движок регистрируется после ответа stopped — форма входа появляется без перемонтирования', async () => {
		let running = false;
		const fetch = mockFetch((c) =>
			c.url === '/api/v1/accounts/1/engine/status'
				? json({ ...fixture<EngineStatus>('engine_status'), running })
				: json(st(running ? 'unauthorized' : 'stopped'))
		);
		const api = createAccountApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const engine = new EngineStore(api);
		await engine.load();
		render(TelegramView, { api, engine });
		expect(await screen.findByText('движок не запущен')).toBeInTheDocument();
		expect(screen.queryByLabelText('Телефон аккаунта')).not.toBeInTheDocument();
		// Опрос статуса движка без перемены не перечитывает статус входа.
		await engine.load();
		const tgReads = () => fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/tg/status').length;
		await waitFor(() => expect(tgReads()).toBe(1));
		// Хост поднял движок: статус движка (пока аккаунт запускается — раз в 2 с) говорит «запущен».
		running = true;
		await engine.load();
		expect(await screen.findByLabelText('Телефон аккаунта')).toBeInTheDocument();
		expect(tgReads()).toBe(2);
	});

	it('online с прода — выход', async () => {
		setup(() => json(fixture('tg_status')));
		// Пользователь входа и привязка аккаунта.
		expect(await screen.findAllByText('267519921')).toHaveLength(2);
		expect(screen.getByRole('button', { name: 'Выйти из Telegram' })).toBeInTheDocument();
	});

	it('перегрузка — пояснение, без формы входа', async () => {
		setup(() => json(st('overload', { user_id: 267519921 })));
		expect(await screen.findByText('перегрузка')).toBeInTheDocument();
		expect(screen.getByText(/приём приостановлен/)).toBeInTheDocument();
		expect(screen.queryByLabelText('Телефон аккаунта')).not.toBeInTheDocument();
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

	it('лимит кодов входа — 429 со своим текстом; свой чат в настройках — пояснение', async () => {
		const user = userEvent.setup();
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('error', { error: 'chat_is_self' }));
			return json({ detail: 'tg_code_rate_limited' }, 429, { 'Retry-After': '1800' });
		});
		expect(await screen.findByText(/указан этот же пользователь Telegram/)).toBeInTheDocument();
		await user.type(screen.getByLabelText('Телефон аккаунта'), '+7999');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		expect(await screen.findByRole('alert')).toHaveTextContent(
			'Слишком много запросов кода входа — следующий через 30 мин'
		);
	});

	it('экран Telegram объясняет постоянную привязку', async () => {
		setup(() => json(fixture('tg_status')));
		const note = await screen.findByText(/навсегда привязан к пользователю Telegram/);
		expect(note).toHaveTextContent(
			'Аккаунт навсегда привязан к пользователю Telegram 267519921. Другой персонаж — это новый аккаунт'
		);
	});

	it('без привязки — предупреждение перед первым входом', async () => {
		setup(() => json(st('unauthorized')));
		expect(await screen.findByLabelText('Телефон аккаунта')).toBeInTheDocument();
		expect(screen.getByText(/Первый вход навсегда привяжет аккаунт/)).toBeInTheDocument();
		expect(screen.queryByText(/навсегда привязан к пользователю/)).not.toBeInTheDocument();
	});

	it('движок не запущен — формы входа нет, привязка видна', async () => {
		setup(() => json(st('stopped', { bound_user_id: 267519921 })));
		expect(await screen.findByText(/навсегда привязан к пользователю Telegram/)).toHaveTextContent('267519921');
		expect(screen.getByText('движок не запущен')).toBeInTheDocument();
		expect(screen.queryByLabelText('Телефон аккаунта')).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Получить код' })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Выйти из Telegram' })).not.toBeInTheDocument();
	});

	it('отказ входа: чужой пользователь, занятый другим аккаунтом, свой чат', async () => {
		const status = { value: st('error', { error: 'unexpected_user', bound_user_id: 267519921 }) };
		setup(() => json(status.value));
		expect(await screen.findByText(/привязан к пользователю Telegram 267519921, а вошёл другой/)).toBeInTheDocument();
		cleanup();

		status.value = st('error', { error: 'tg_user_taken' });
		setup(() => json(status.value));
		expect(await screen.findByText(/уже привязан к другому аккаунту/)).toBeInTheDocument();
		cleanup();

		status.value = st('error', { error: 'chat_is_self' });
		setup(() => json(status.value));
		expect(await screen.findByText(/указан этот же пользователь Telegram/)).toBeInTheDocument();
	});
});
