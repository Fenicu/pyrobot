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
		await user.click(screen.getByRole('button', { name: 'Войти' }));
		expect(await screen.findByText('Неверный код — попробуйте ещё раз')).toBeInTheDocument();
		expect(screen.getByLabelText('Код из Telegram')).toHaveValue('');
		await user.type(screen.getByLabelText('Код из Telegram'), '22222');
		await user.click(screen.getByRole('button', { name: 'Войти' }));
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
		await user.click(screen.getByRole('button', { name: 'Войти' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Попытка входа устарела');
		expect(await screen.findByLabelText('Телефон аккаунта')).toBeInTheDocument();
	});

	it('неизвестный код ошибки в статусе — тот же текст, что у неизвестного отказа 400', async () => {
		setup(() => json(st('error', { error: 'auth_restart' })));
		expect(await screen.findByText('Telegram отклонил запрос (auth_restart)')).toBeInTheDocument();
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

	it('адрес почты отклонён сервером (422 invalid_email) — тот же шаг', async () => {
		const user = userEvent.setup();
		let started = false;
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/login/email') return json({ detail: 'invalid_email' }, 422);
			if (c.url === '/api/v1/accounts/1/tg/status' && !started) return json(st('unauthorized'));
			started = true;
			return json(st('awaiting_email', { attempt_id: 'a1', delivery_type: 'setup_email' }));
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+79991234567');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		await user.type(await screen.findByLabelText('Электронная почта'), 'a@b');
		await user.click(screen.getByRole('button', { name: 'Отправить код на почту' }));
		expect(await screen.findByText('Некорректный адрес почты')).toBeInTheDocument();
		expect(screen.getByLabelText('Электронная почта')).toBeInTheDocument();
	});

	it('вход через почту (setup_email → email_code → code → online)', async () => {
		const user = userEvent.setup();
		const fetch = setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start')
				return json(st('awaiting_email', { attempt_id: 'a1', delivery_type: 'setup_email' }));
			if (c.url === '/api/v1/accounts/1/tg/login/email')
				return json(
					st('awaiting_email_code', {
						attempt_id: 'a1',
						delivery_email_pattern: 't***@e***.com'
					})
				);
			if (c.url === '/api/v1/accounts/1/tg/login/email-code')
				return json(st('awaiting_code', { attempt_id: 'a1', delivery_type: 'app' }));
			if (c.url === '/api/v1/accounts/1/tg/login/code')
				return json(st('online', { user_id: 267519921 }));
			return json(st('unauthorized'));
		});

		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+79991234567');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		expect(
			await screen.findByText(/требует привязать адрес электронной почты/)
		).toBeInTheDocument();
		const emailInput = screen.getByLabelText('Электронная почта');
		await user.type(emailInput, 'test@example.com');
		await user.click(screen.getByRole('button', { name: 'Отправить код на почту' }));

		expect(
			await screen.findByText('Код подтверждения отправлен на почту t***@e***.com')
		).toBeInTheDocument();
		const emailCodeInput = screen.getByLabelText('Код из почты');
		await user.type(emailCodeInput, '54321');
		await user.click(screen.getByRole('button', { name: 'Подтвердить почту' }));

		expect(await screen.findByText(/^Код отправлен в приложение Telegram/)).toBeInTheDocument();
		const codeInput = screen.getByLabelText('Код из Telegram');
		await user.type(codeInput, '12345');
		await user.click(screen.getByRole('button', { name: 'Войти' }));

		expect(await screen.findByRole('button', { name: 'Выйти из Telegram' })).toBeInTheDocument();

		const posts = fetch.calls.filter((c) => c.method === 'POST').map((c) => ({ url: c.url, body: JSON.parse(c.body) }));
		expect(posts).toEqual([
			{ url: '/api/v1/accounts/1/tg/login/start', body: { phone: '+79991234567' } },
			{ url: '/api/v1/accounts/1/tg/login/email', body: { attempt_id: 'a1', email: 'test@example.com' } },
			{ url: '/api/v1/accounts/1/tg/login/email-code', body: { attempt_id: 'a1', code: '54321' } },
			{ url: '/api/v1/accounts/1/tg/login/code', body: { attempt_id: 'a1', code: '12345' } }
		]);
	});

	it('повторная отправка кода (resend_code) и способы доставки кода', async () => {
		const user = userEvent.setup();
		let resent = false;
		const fetch = setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start')
				return json(
					st('awaiting_code', {
						attempt_id: 'a1',
						delivery_type: 'app',
						delivery_next_type: 'sms',
						delivery_timeout: 0
					})
				);
			if (c.url === '/api/v1/accounts/1/tg/login/resend') {
				resent = true;
				return json(
					st('awaiting_code', {
						attempt_id: 'a1',
						delivery_type: 'sms',
						delivery_next_type: null
					})
				);
			}
			return json(st('unauthorized'));
		});

		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+79991234567');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		expect(await screen.findByText(/^Код отправлен в приложение Telegram/)).toBeInTheDocument();
		const resendBtn = screen.getByRole('button', { name: 'Отправить код по SMS' });
		expect(resendBtn).toBeEnabled();

		await user.click(resendBtn);
		expect(resent).toBe(true);
		expect(await screen.findByText('Код отправлен по SMS')).toBeInTheDocument();
		// Следующего способа доставки нет — и кнопки повторной отправки нет.
		expect(screen.queryByRole('button', { name: /^Отправить код/ })).not.toBeInTheDocument();

		const resendCall = fetch.calls.find((c) => c.url === '/api/v1/accounts/1/tg/login/resend');
		expect(JSON.parse(resendCall!.body)).toEqual({ attempt_id: 'a1' });
	});

	it('без следующего способа доставки кнопки повторной отправки нет; send_code_unavailable — пояснение', async () => {
		const user = userEvent.setup();
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start')
				return json(st('awaiting_code', { attempt_id: 'a1', delivery_type: 'app', delivery_next_type: 'sms' }));
			return json(
				st('awaiting_code', {
					attempt_id: 'a1',
					delivery_type: 'app',
					delivery_next_type: null,
					error: 'send_code_unavailable'
				})
			);
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+7999');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		await user.click(await screen.findByRole('button', { name: 'Отправить код по SMS' }));
		expect(await screen.findByText(/Других способов отправки кода у Telegram нет/)).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /^Отправить код/ })).not.toBeInTheDocument();
		expect(screen.getByLabelText('Код из Telegram')).toBeInTheDocument();
	});

	it('код отправлен на почту (delivery_type: email)', async () => {
		const user = userEvent.setup();
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start')
				return json(
					st('awaiting_code', {
						attempt_id: 'a1',
						delivery_type: 'email',
						delivery_email_pattern: 'f***n@g***.com'
					})
				);
			return json(st('unauthorized'));
		});

		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+79991234567');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));

		expect(await screen.findByText('Код отправлен на почту f***n@g***.com')).toBeInTheDocument();
	});

	it.each([
		['firebase_sms', 'Код отправлен по SMS'],
		['sms_word', 'Код отправлен по SMS — секретное слово из сообщения'],
		['sms_phrase', 'Код отправлен по SMS — секретная фраза из сообщения']
	])('код по SMS (delivery_type: %s)', async (type, text) => {
		const user = userEvent.setup();
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start')
				return json(st('awaiting_code', { attempt_id: 'a1', delivery_type: type }));
			return json(st('unauthorized'));
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+79991234567');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		expect(await screen.findByText(text)).toBeInTheDocument();
	});

	it('перевод ошибок Telegram: RPC ID, send_code_unsupported, неизвестный отказ, 502 почты', async () => {
		const user = userEvent.setup();
		let error = 'phone_number_invalid';
		let status = 400;
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(st('unauthorized'));
			if (c.url === '/api/v1/accounts/1/tg/login/start') return json({ detail: error }, status);
			return json(st('unauthorized'));
		});

		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+7999');
		const expectAlert = async (code: string, text: string, http = 400) => {
			error = code;
			status = http;
			await user.click(screen.getByRole('button', { name: 'Получить код' }));
			await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(text));
		};
		await expectAlert('phone_number_invalid', 'Неверный номер телефона');
		// Сервер отдаёт чистый RPC ID Telegram в нижнем регистре.
		await expectAlert('phone_number_banned', 'Номер телефона заблокирован в Telegram');
		await expectAlert('phone_password_flood', 'Слишком много попыток ввода пароля — попробуйте позже');
		await expectAlert(
			'send_code_unsupported:sent_code_payment_required',
			'Telegram ответил на запрос кода способом, который бот не поддерживает (sent_code_payment_required)'
		);
		await expectAlert('auth_restart', 'Telegram отклонил запрос (auth_restart)');
		await expectAlert('send_verify_email_code_failed', 'Не удалось отправить письмо с кодом', 502);
		await expectAlert('verify_email_failed', 'Не удалось подтвердить почту', 502);
	});
});

describe('Код не пришёл', () => {
	const awaiting = (extra: object = {}) =>
		st('awaiting_code', { attempt_id: 'a1', delivery_type: 'app', delivery_next_type: null, ...extra });

	it('код через серверное приложение: куда отправлен, «Код не пришёл?», отмена входа', async () => {
		const user = userEvent.setup();
		let status = st('unauthorized', { app: 'server' });
		const fetch = setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/login/start') status = awaiting({ app: 'server' });
			if (c.url === '/api/v1/accounts/1/tg/login/cancel') status = st('unauthorized', { app: 'server' });
			return json(status);
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+7999');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		expect(
			await screen.findByText(
				'Код отправлен в приложение Telegram — сообщение от «Telegram» с синей галочкой на ваших устройствах; может быть в архиве'
			)
		).toBeInTheDocument();
		expect(screen.getByText('Код запрошен через серверное приложение')).toBeInTheDocument();
		const hint = screen.getByText('Код не пришёл?');
		await user.click(hint);
		const details = hint.closest('details')!;
		expect(details).toHaveTextContent('Telegram иногда не доставляет коды для общего приложения сервера');
		expect(details).toHaveTextContent('API development tools');
		expect(details).toHaveTextContent('код придёт в приложение Telegram');
		expect(details).toHaveTextContent('App title — любое');
		expect(details).toHaveTextContent('Short name — 5–32 латинских букв или цифр');
		expect(details).toHaveTextContent('Platform — любая (например, Desktop)');
		expect(details).toHaveTextContent('«Create application»');
		expect(details).toHaveTextContent('скопируйте api_id и api_hash');
		const link = details.querySelector('a[href="https://my.telegram.org"]');
		expect(link).not.toBeNull();
		expect(link).toHaveAttribute('target', '_blank');
		expect(link).toHaveAttribute('rel', 'noopener noreferrer');

		await user.click(screen.getByRole('button', { name: 'Отменить вход' }));
		expect(await screen.findByLabelText('Телефон аккаунта')).toBeInTheDocument();
		const cancel = fetch.calls.find((c) => c.url === '/api/v1/accounts/1/tg/login/cancel')!;
		expect([cancel.method, cancel.headers.get('x-csrf-token')]).toEqual(['POST', 'c']);
		// После отмены форма своего приложения доступна.
		expect(screen.getByLabelText('api_id')).toBeInTheDocument();
	});

	it('код через своё приложение — api_id и без «Код не пришёл?»', async () => {
		const user = userEvent.setup();
		let status = st('unauthorized', { app: { api_id: 12345 } });
		setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/login/start') status = awaiting({ app: { api_id: 12345 } });
			return json(status);
		});
		await user.type(await screen.findByLabelText('Телефон аккаунта'), '+7999');
		await user.click(screen.getByRole('button', { name: 'Получить код' }));
		expect(await screen.findByText('Код запрошен через своё приложение (api_id 12345)')).toBeInTheDocument();
		expect(screen.queryByText('Код не пришёл?')).not.toBeInTheDocument();
	});

	it('вход из другой вкладки и шаг почты тоже можно отменить', async () => {
		setup(() => json(st('awaiting_email', { attempt_id: 'other', delivery_type: 'setup_email' })));
		expect(await screen.findByText(/Вход уже начат в другой вкладке/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Отменить вход' })).toBeInTheDocument();
	});

	it('блок своего приложения — короткая инструкция и ссылка на my.telegram.org', async () => {
		setup(() => json(st('unauthorized', { app: 'server' })));
		await screen.findByLabelText('api_id');
		const block = screen.getByRole('heading', { name: 'Своё приложение Telegram' }).closest('section')!;
		expect(block).toHaveTextContent('Если код входа не приходит');
		expect(block).toHaveTextContent('API development tools');
		expect(block).toHaveTextContent('Short name — 5–32 латинских букв или цифр');
		expect(block).toHaveTextContent('«Create application»');
		expect(block.querySelector('a[href="https://my.telegram.org"]')).not.toBeNull();
	});
});

describe('Своё приложение Telegram', () => {
	const HASH = '0123456789abcdef0123456789abcdef';

	it('своё приложение: форма только после выхода, hash не показывается', async () => {
		const user = userEvent.setup();
		// В сети — подсказка вместо формы и «Убрать».
		setup(() => json(st('online', { user_id: 267519921, app: 'server' })));
		expect(await screen.findByText('Серверное приложение')).toBeInTheDocument();
		expect(screen.getByText('Сначала выйдите из Telegram')).toBeInTheDocument();
		expect(screen.queryByLabelText('api_id')).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Убрать' })).not.toBeInTheDocument();
		cleanup();

		// Вышел — форма; после сохранения статус перечитан, hash не показывается и не хранится.
		let status = st('unauthorized', { app: 'server' });
		const fetch = setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(status);
			status = st('unauthorized', { app: { api_id: 12345 } });
			return new Response(null, { status: 204 });
		});
		await user.type(await screen.findByLabelText('api_id'), '12345');
		await user.type(screen.getByLabelText('api_hash'), HASH);
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		const put = fetch.calls.find((c) => c.method === 'PUT')!;
		expect([put.url, JSON.parse(put.body), put.headers.get('x-csrf-token')]).toEqual([
			'/api/v1/accounts/1/tg/app',
			{ api_id: 12345, api_hash: HASH },
			'c'
		]);
		expect(await screen.findByText('Своё: api_id 12345')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Убрать' })).toBeInTheDocument();
		expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/tg/status')).toHaveLength(2);
		expect(screen.getByLabelText('api_hash')).toHaveValue('');
		expect(document.body.textContent).not.toContain(HASH);
	});

	it('клиентская проверка: пустое, дробное и за границей api_id, не тот hash — без запроса', async () => {
		const user = userEvent.setup();
		const fetch = setup(() => json(st('unauthorized', { app: 'server' })));
		const id = await screen.findByLabelText('api_id');
		const hash = screen.getByLabelText('api_hash');
		const save = screen.getByRole('button', { name: 'Сохранить' });
		// Пустые значения не уходят на сервер: кнопка неактивна.
		expect(save).toBeDisabled();
		await user.type(id, '12345');
		expect(save).toBeDisabled();
		await user.type(hash, HASH);
		expect(save).toBeEnabled();

		await user.clear(id);
		await user.type(id, '1.5');
		await user.click(save);
		expect(await screen.findByRole('alert')).toHaveTextContent('api_id — целое число от 1 до 2147483647');

		await user.clear(id);
		await user.type(id, '2147483648');
		await user.click(save);
		await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('api_id — целое число от 1 до 2147483647'));

		await user.clear(id);
		await user.type(id, '12345');
		await user.clear(hash);
		await user.type(hash, 'xyz');
		await user.click(save);
		await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('api_hash — 32 шестнадцатеричных символа'));
		expect(fetch.calls.filter((c) => c.method === 'PUT')).toHaveLength(0);
	});

	it('экспонента, hex и плюс не проходят как api_id — без запроса', async () => {
		const user = userEvent.setup();
		const fetch = setup(() => json(st('unauthorized', { app: 'server' })));
		const id = await screen.findByLabelText('api_id');
		await user.type(screen.getByLabelText('api_hash'), HASH);
		for (const bad of ['1e3', '0x1F', '+5']) {
			await user.clear(id);
			await user.type(id, bad);
			await user.click(screen.getByRole('button', { name: 'Сохранить' }));
			expect(await screen.findByRole('alert')).toHaveTextContent(
				'api_id — целое число от 1 до 2147483647'
			);
		}
		expect(fetch.calls.filter((c) => c.method === 'PUT')).toHaveLength(0);
	});

	it('«Убрать» возвращает серверное приложение', async () => {
		const user = userEvent.setup();
		let status = st('unauthorized', { app: { api_id: 12345 } });
		const fetch = setup((c) => {
			if (c.url === '/api/v1/accounts/1/tg/status') return json(status);
			status = st('unauthorized', { app: 'server' });
			return new Response(null, { status: 204 });
		});
		expect(await screen.findByText('Своё: api_id 12345')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Убрать' }));
		const del = fetch.calls.find((c) => c.method === 'DELETE')!;
		expect([del.url, del.headers.get('x-csrf-token')]).toEqual(['/api/v1/accounts/1/tg/app', 'c']);
		expect(await screen.findByText('Серверное приложение')).toBeInTheDocument();
		expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/tg/status')).toHaveLength(2);
	});

	it('409 tg_logged_in при сохранении — текст, статус перечитан, форма скрыта', async () => {
		const user = userEvent.setup();
		let status = st('unauthorized', { app: 'server' });
		const fetch = setup((c) => {
			if (c.method === 'PUT') return json({ detail: 'tg_logged_in' }, 409);
			const cur = status;
			// Вход успел завершиться посреди смены: перечитанный статус — онлайн.
			status = st('online', { user_id: 267519921, app: 'server' });
			return json(cur);
		});
		await user.type(await screen.findByLabelText('api_id'), '12345');
		await user.type(screen.getByLabelText('api_hash'), HASH);
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Вход в Telegram уже выполнен');
		expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/tg/status')).toHaveLength(2);
		expect(screen.queryByLabelText('api_id')).not.toBeInTheDocument();
		expect(screen.getByText('Сначала выйдите из Telegram')).toBeInTheDocument();
	});

	it('движок остановлен, вход в базе: 409 — подсказка вместо формы', async () => {
		const user = userEvent.setup();
		// Движок не запущен: статус «stopped» не отличает вошедшего от вышедшего, сервер
		// отказывает по сессии в базе — и статус после перечитывания остаётся «stopped».
		const status = st('stopped', { app: { api_id: 12345 } });
		const fetch = setup((c) => {
			if (c.method === 'PUT') return json({ detail: 'tg_logged_in' }, 409);
			return json(status);
		});
		await user.type(await screen.findByLabelText('api_id'), '99999');
		await user.type(screen.getByLabelText('api_hash'), HASH);
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		expect(await screen.findByRole('alert')).toHaveTextContent('Вход в Telegram уже выполнен');
		expect(fetch.calls.filter((c) => c.url === '/api/v1/accounts/1/tg/status')).toHaveLength(2);
		// Подсказка вместо формы и «Убрать»: менять приложение нельзя, пока есть вход.
		expect(screen.getByText('Сначала выйдите из Telegram')).toBeInTheDocument();
		expect(screen.queryByLabelText('api_id')).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: 'Убрать' })).not.toBeInTheDocument();
	});
});
