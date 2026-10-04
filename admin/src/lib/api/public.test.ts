import { describe, expect, it } from 'vitest';
import { ApiFailure } from './errors';
import { acceptInvite, peekInvite, recoverFinish, recoverStart } from './public';
import { json, mockFetch } from '$lib/test/fetch';

describe('public API', () => {
	it('peekInvite: успех и ошибки 404 / 410', async () => {
		const fetch = mockFetch((c) => {
			if (c.url === '/api/v1/invites/good') return json({ expires_at: '2026-10-10T00:00:00Z' });
			if (c.url === '/api/v1/invites/used') return json({ detail: 'invite_gone' }, 410);
			return json({ detail: 'invite_not_found' }, 404);
		});

		const ok = await peekInvite('good', fetch);
		expect(ok).toEqual({ expires_at: '2026-10-10T00:00:00Z' });

		await expect(peekInvite('unknown', fetch)).rejects.toThrow(ApiFailure);
		await expect(peekInvite('unknown', fetch)).rejects.toThrow('Приглашение не найдено');

		await expect(peekInvite('used', fetch)).rejects.toThrow(ApiFailure);
		await expect(peekInvite('used', fetch)).rejects.toThrow('Приглашение уже использовано, отозвано или истекло');
	});

	it('acceptInvite: 201 возвращает токены и коды, 409 даёт ошибку занятого логина', async () => {
		const fetch = mockFetch((c) => {
			if (c.method !== 'POST') return json({ detail: 'not allowed' }, 405);
			const body = JSON.parse(c.body);
			if (body.login === 'taken') return json({ detail: 'login_taken' }, 409);
			return json(
				{
					login: body.login,
					csrf_token: 'csrf-123',
					role: 'user',
					recovery_codes: ['code-1', 'code-2']
				},
				201
			);
		});

		const res = await acceptInvite('token-1', 'newuser', 'password12345', fetch);
		expect(res).toEqual({
			login: 'newuser',
			csrf_token: 'csrf-123',
			role: 'user',
			recovery_codes: ['code-1', 'code-2']
		});

		await expect(acceptInvite('token-1', 'taken', 'password12345', fetch)).rejects.toThrow('Логин уже занят');
	});

	it('recoverStart: 202 при успехе, 429 при лимите', async () => {
		const fetch = mockFetch((c) => {
			const body = JSON.parse(c.body);
			if (body.login === 'spammer') return json({ detail: 'rate_limited' }, 429);
			return json({}, 202);
		});

		await expect(recoverStart('alice', fetch)).resolves.toBeUndefined();
		await expect(recoverStart('spammer', fetch)).rejects.toThrow(ApiFailure);
	});

	it('recoverFinish: 200 возвращает MeOut, 403 даёт неверный код', async () => {
		const fetch = mockFetch((c) => {
			const body = JSON.parse(c.body);
			if (body.code === 'wrong' || body.recovery_code === 'wrong') {
				return json({ detail: 'invalid_code' }, 403);
			}
			return json({ login: body.login, csrf_token: 'csrf-after-recover', role: 'user' });
		});

		const res = await recoverFinish(
			{ login: 'bob', code: '12345678', password: 'newpassword123' },
			fetch
		);
		expect(res).toEqual({ login: 'bob', csrf_token: 'csrf-after-recover', role: 'user' });

		await expect(
			recoverFinish({ login: 'bob', code: 'wrong', password: 'newpassword123' }, fetch)
		).rejects.toThrow('Неверный код');
	});

	it('сбой сети бросает ApiFailure c kind network', async () => {
		const fetch = (() => Promise.reject(new TypeError('offline'))) as typeof window.fetch;
		await expect(peekInvite('token', fetch)).rejects.toThrow(ApiFailure);
	});
});
