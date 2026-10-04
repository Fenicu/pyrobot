import { describe, expect, it, vi } from 'vitest';
import { deferred, flush } from '$lib/test/deferred';
import { json, mockFetch } from '$lib/test/fetch';
import { Session } from './session.svelte';

/** Управляемые таймеры: тест сам запускает отложенный повтор. */
function manualTimers() {
	const pending: { id: number; fn: () => void; ms: number }[] = [];
	let seq = 0;
	return {
		pending,
		setTimer: (fn: () => void, ms: number) => {
			pending.push({ id: ++seq, fn, ms });
			return seq;
		},
		clearTimer: (handle: unknown) => {
			const i = pending.findIndex((t) => t.id === handle);
			if (i !== -1) pending.splice(i, 1);
		},
		async fire() {
			pending.shift()!.fn();
			await flush();
		}
	};
}

describe('сессия', () => {
	it('/auth/me даёт логин и CSRF в память, 401 — аноним', async () => {
		let authed = true;
		const fetch = mockFetch(() =>
			authed ? json({ login: 'admin', csrf_token: 'c1' }) : json({ detail: 'not authenticated' }, 401)
		);
		const s = new Session(fetch);
		expect(await s.load()).toBe('ok');
		expect([s.status, s.login, s.csrf, s.role]).toEqual(['authenticated', 'admin', 'c1', null]);
		authed = false;
		expect(await s.load()).toBe('unauthorized');
		expect([s.status, s.login, s.csrf, s.role]).toEqual(['anonymous', null, null, null]);
	});

	it('adopt устанавливает логин, CSRF и роль', () => {
		const s = new Session();
		expect([s.status, s.role]).toEqual(['unknown', null]);
		s.adopt({ login: 'alice', csrf_token: 'c-adopt', role: 'user' });
		expect([s.status, s.login, s.csrf, s.role]).toEqual(['authenticated', 'alice', 'c-adopt', 'user']);
		s.clear();
		expect([s.status, s.login, s.csrf, s.role]).toEqual(['anonymous', null, null, null]);
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
		expect(s.status).toBe('unknown');
	});
});

describe('старт без связи', () => {
	it('сеть или 5xx — статус остаётся unknown и повтор с растущей паузой, аноним — только по 401', async () => {
		const replies: (Response | Error)[] = [
			json({ detail: 'bad gateway' }, 502),
			new TypeError('offline'),
			json({ detail: 'oops' }, 500),
			json({ detail: 'not authenticated' }, 401)
		];
		const fetch = mockFetch(() => {
			const r = replies.shift()!;
			if (r instanceof Error) throw r;
			return r;
		});
		const t = manualTimers();
		const s = new Session(fetch, () => {}, t);
		await s.start();
		expect([s.status, s.offline, s.retryIn]).toEqual(['unknown', true, 1000]);
		expect(t.pending.map((p) => p.ms)).toEqual([1000]);
		await t.fire();
		expect([s.status, s.offline, s.retryIn]).toEqual(['unknown', true, 2000]);
		await t.fire();
		expect([s.status, s.retryIn]).toEqual(['unknown', 4000]);
		await t.fire();
		expect([s.status, s.offline, s.retryIn]).toEqual(['anonymous', false, 0]);
		expect(t.pending).toEqual([]);
		expect(fetch.calls).toHaveLength(4);
	});

	it('пауза растёт до 30 с', async () => {
		const t = manualTimers();
		const s = new Session((() => Promise.reject(new TypeError('offline'))) as typeof fetch, () => {}, t);
		await s.start();
		const waits = [s.retryIn];
		for (let i = 0; i < 6; i++) {
			await t.fire();
			waits.push(s.retryIn);
		}
		expect(waits).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
	});

	it('«Повторить» — сразу и без второго таймера, пока запрос идёт — не дублируется', async () => {
		const reply = deferred<Response>();
		const fetch = mockFetch(() => (fetch.calls.length === 1 ? json({ detail: '' }, 502) : reply.promise));
		const t = manualTimers();
		const s = new Session(fetch, () => {}, t);
		await s.start();
		expect(t.pending).toHaveLength(1);
		s.retry();
		s.retry();
		await flush();
		expect(t.pending).toHaveLength(0);
		expect(fetch.calls).toHaveLength(2);
		reply.resolve(json({ login: 'admin', csrf_token: 'c1' }));
		await flush();
		expect([s.status, s.offline, s.retryIn]).toEqual(['authenticated', false, 0]);
		expect(t.pending).toHaveLength(0);
	});

	it('вход во время паузы снимает повтор', async () => {
		const fetch = mockFetch((c) =>
			c.url === '/api/v1/auth/login' ? json({ login: 'admin', csrf_token: 'c9' }) : json({ detail: '' }, 503)
		);
		const t = manualTimers();
		const s = new Session(fetch, () => {}, t);
		await s.start();
		expect(s.offline).toBe(true);
		expect(await s.signIn('admin', 'right')).toBeNull();
		expect([s.status, s.offline, s.retryIn]).toEqual(['authenticated', false, 0]);
		expect(t.pending).toEqual([]);
	});
});
