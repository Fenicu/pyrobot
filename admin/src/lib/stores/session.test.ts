import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch } from '$lib/test/fetch';
import { Session } from './session.svelte';

describe('сессия', () => {
	it('/auth/me даёт логин и CSRF в память, 401 — аноним', async () => {
		let authed = true;
		const fetch = mockFetch(() =>
			authed ? json({ login: 'admin', csrf_token: 'c1' }) : json({ detail: 'not authenticated' }, 401)
		);
		const s = new Session(fetch);
		expect(await s.load()).toBe('ok');
		expect([s.status, s.login, s.csrf]).toEqual(['authenticated', 'admin', 'c1']);
		authed = false;
		expect(await s.load()).toBe('unauthorized');
		expect([s.status, s.login, s.csrf]).toEqual(['anonymous', null, null]);
	});

	it('выход считается выполненным только при 204 или 401', async () => {
		const answers = [
			json({ detail: 'csrf token mismatch' }, 403),
			new Response(null, { status: 204 })
		];
		const fetch = mockFetch((c) =>
			c.url === '/api/v1/auth/logout' ? answers.shift()! : json({ login: 'admin', csrf_token: 'c2' })
		);
		const s = new Session(fetch);
		await s.load();
		expect(await s.signOut()).toEqual({ kind: 'csrf', status: 403 });
		// Сессия жива, токен перечитан — повтор уходит со свежим.
		expect([s.status, s.csrf]).toEqual(['authenticated', 'c2']);
		expect(await s.signOut()).toBeNull();
		expect([s.status, s.csrf]).toEqual(['anonymous', null]);
		const gone = new Session(
			mockFetch((c) =>
				c.url === '/api/v1/auth/logout'
					? json({ detail: 'not authenticated' }, 401)
					: json({ login: 'admin', csrf_token: 'c1' })
			)
		);
		await gone.load();
		expect(await gone.signOut()).toBeNull();
		expect(gone.status).toBe('anonymous');
	});

	it('сбой сети при выходе — сессия остаётся, ошибка для повтора', async () => {
		let offline = false;
		const s = new Session(((input: RequestInfo | URL, init?: RequestInit) =>
			offline
				? Promise.reject(new TypeError('offline'))
				: mockFetch(() => json({ login: 'admin', csrf_token: 'c1' }))(input, init)) as typeof fetch);
		await s.load();
		offline = true;
		expect(await s.signOut()).toMatchObject({ kind: 'network' });
		expect([s.status, s.csrf]).toEqual(['authenticated', 'c1']);
		const failing = new Session(
			mockFetch((c) =>
				c.url === '/api/v1/auth/logout' ? json({ detail: 'oops' }, 500) : json({ login: 'admin', csrf_token: 'c1' })
			)
		);
		await failing.load();
		expect(await failing.signOut()).toMatchObject({ kind: 'http', status: 500 });
		expect(failing.status).toBe('authenticated');
	});

	it('вход и выход: CSRF уходит в logout и забывается', async () => {
		const fetch = mockFetch((c) =>
			c.url === '/api/v1/auth/login'
				? JSON.parse(c.body).password === 'right'
					? json({ login: 'admin', csrf_token: 'c9' })
					: json({ detail: 'invalid credentials' }, 401)
				: new Response(null, { status: 204 })
		);
		const s = new Session(fetch);
		expect(await s.signIn('admin', 'wrong')).toEqual({ kind: 'unauthorized', status: 401 });
		expect(s.status).toBe('unknown');
		expect(await s.signIn('admin', 'right')).toBeNull();
		expect(s.csrf).toBe('c9');
		await s.signOut();
		expect(fetch.calls.at(-1)?.headers.get('X-CSRF-Token')).toBe('c9');
		expect([s.status, s.csrf]).toEqual(['anonymous', null]);
	});

	it('401 от запроса — токен забыт и переход на вход', async () => {
		const onExpire = vi.fn();
		const s = new Session(
			mockFetch(() => json({ login: 'admin', csrf_token: 'c1' })),
			onExpire
		);
		await s.load();
		s.hooks.unauthorized();
		expect([s.status, s.csrf]).toEqual(['anonymous', null]);
		expect(onExpire).toHaveBeenCalledTimes(1);
		s.hooks.unauthorized();
		expect(onExpire).toHaveBeenCalledTimes(1);
	});

	it('сервер недоступен — не «вышел», а ошибка', async () => {
		const s = new Session((() => Promise.reject(new TypeError('offline'))) as typeof fetch);
		expect(await s.load()).toBe('error');
		expect(s.status).toBe('anonymous');
	});
});
