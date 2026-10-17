import { describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { CommandOut } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { clickButton, commandResult, isStale, sendText } from './commands';

const out = (patch: Partial<CommandOut>): CommandOut => ({
	action_id: 1,
	status: 'rejected',
	reason: '',
	answer: null,
	...patch
});

describe('commandResult', () => {
	it('запрещённая и донат-команда без ручного управления — подсказка взять управление', () => {
		expect(commandResult(out({ reason: 'forbidden' })).text).toBe(
			'Отклонено: команда запрещена для бота — возьмите управление на экране «Игра», чтобы отправить вручную'
		);
		expect(commandResult(out({ reason: 'donate' })).text).toBe(
			'Отклонено: донат-команды бот не отправляет — возьмите управление на экране «Игра», чтобы отправить вручную'
		);
	});

	it('отказ без связи с Telegram — по-русски', () => {
		expect(commandResult(out({ reason: 'tg_unreachable' }))).toEqual({
			text: 'Отклонено: нет связи с Telegram',
			kind: 'error'
		});
	});
});

describe('отправка и нажатие', () => {
	const hooks = { csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} };
	const confirm = {
		code: 'confirm_required', reason: 'missing', confirm_token: 'tok', expires_at: '2026-10-17T10:02:00Z',
		state_version: 5, command_class: 'risky'
	};

	it('sendText: risky — подтверждение и повтор тем же ключом с токеном', async () => {
		const fetch = mockFetch((c) =>
			JSON.parse(c.body).confirm_token
				? json({ action_id: 7, status: 'confirmed', reason: 'reply', answer: null })
				: json({ detail: confirm }, 409)
		);
		const confirmer = vi.fn(async () => true);
		const out = await sendText(createAccountApi(hooks, 1, fetch), '/sells_piper_80', 'send-1', confirmer);
		expect(out?.status).toBe('confirmed');
		const bodies = fetch.calls.map((c) => [c.url, JSON.parse(c.body)]);
		expect(bodies).toEqual([
			['/api/v1/accounts/1/commands/send', { text: '/sells_piper_80', idempotency_key: 'send-1' }],
			['/api/v1/accounts/1/commands/send', { text: '/sells_piper_80', idempotency_key: 'send-1', confirm_token: 'tok' }]
		]);
		expect(confirmer).toHaveBeenCalledWith(confirm, '/sells_piper_80');
	});

	it('clickButton: ревизия сообщения и подпись кнопки в подтверждении', async () => {
		const fetch = mockFetch(() => json({ action_id: 8, status: 'rejected', reason: 'stale_revision', answer: null }));
		const msg = { chat_id: 227859379, msg_id: 3626304, revision: 1790535904 };
		const out = await clickButton(createAccountApi(hooks, 1, fetch), msg, ['В отеле', 1, 0, 'sleep_Hotel', null, null]);
		expect(out && isStale(out)).toBe(true);
		const body = JSON.parse(fetch.calls[0]!.body);
		expect(fetch.calls[0]!.url).toBe('/api/v1/accounts/1/commands/click');
		expect(body).toMatchObject({ chat_id: 227859379, message_id: 3626304, revision: 1790535904, callback_data: 'sleep_Hotel' });
		expect(body.idempotency_key).toMatch(/^click-/);
	});

	it('отказ из-за блока трат — по-русски', () => {
		expect(commandResult(out({ reason: 'blocked:reconcile_required' })).text).toBe('Отклонено: траты заблокированы до сверки');
		expect(commandResult(out({ reason: 'blocked:metro' })).text).toBe('Отклонено: траты заблокированы: metro');
	});
});
