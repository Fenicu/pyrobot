import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch } from '$lib/test/fetch';
import { call, createApi, type SessionHooks } from './client';
import { ApiFailure } from './errors';

function hooks(token: string | null = 'tok-1', fresh: string | null = 'tok-2') {
	return {
		csrf: vi.fn(() => token),
		refreshCsrf: vi.fn(async () => fresh),
		unauthorized: vi.fn()
	} satisfies SessionHooks;
}

describe('клиент API', () => {
	it('CSRF только на изменяющих запросах', async () => {
		const fetch = mockFetch(() => json({ ok: true }));
		const api = createApi(hooks(), fetch);
		await api.GET('/api/v1/engine/status');
		await api.POST('/api/v1/engine/pause');
		const [get, post] = fetch.calls;
		expect(get?.headers.get('X-CSRF-Token')).toBeNull();
		expect(post?.headers.get('X-CSRF-Token')).toBe('tok-1');
	});

	it('401 — сессия закончилась, кроме входа и /auth/me', async () => {
		const h = hooks();
		const api = createApi(
			h,
			mockFetch(() => json({ detail: 'not authenticated' }, 401))
		);
		await expect(call(api.GET('/api/v1/state'))).rejects.toMatchObject({
			error: { kind: 'unauthorized' }
		});
		expect(h.unauthorized).toHaveBeenCalledTimes(1);
		await api.GET('/api/v1/auth/me');
		await api.POST('/api/v1/auth/login', { body: { login: 'a', password: 'b' } });
		expect(h.unauthorized).toHaveBeenCalledTimes(1);
	});

	it('403 CSRF — перечитать токен и повторить один раз с тем же телом', async () => {
		const h = hooks();
		const fetch = mockFetch((c) =>
			c.headers.get('X-CSRF-Token') === 'tok-2'
				? json({ action_id: 5, status: 'confirmed', reason: 'reply', answer: null })
				: json({ detail: 'csrf token mismatch' }, 403)
		);
		const api = createApi(h, fetch);
		const out = await call(
			api.POST('/api/v1/commands/send', { body: { text: '/inv', idempotency_key: 'k1' } })
		);
		expect(out.status).toBe('confirmed');
		expect(h.refreshCsrf).toHaveBeenCalledTimes(1);
		expect(fetch.calls.map((c) => c.headers.get('X-CSRF-Token'))).toEqual(['tok-1', 'tok-2']);
		expect(JSON.parse(fetch.calls[1]?.body ?? '')).toEqual({ text: '/inv', idempotency_key: 'k1' });
	});

	it('403 CSRF дважды — не зацикливается', async () => {
		const h = hooks();
		const fetch = mockFetch(() => json({ detail: 'csrf token mismatch' }, 403));
		const api = createApi(h, fetch);
		await expect(call(api.POST('/api/v1/engine/pause'))).rejects.toMatchObject({
			error: { kind: 'csrf' }
		});
		expect(fetch.calls).toHaveLength(2);
	});

	it('403 запрета не повторяется', async () => {
		const h = hooks();
		const fetch = mockFetch(() => json({ detail: 'forbidden' }, 403));
		const api = createApi(h, fetch);
		const failure = await call(
			api.POST('/api/v1/commands/send', { body: { text: '/givemoney', idempotency_key: 'k' } })
		).catch((e: unknown) => e);
		expect(failure).toBeInstanceOf(ApiFailure);
		expect((failure as ApiFailure).error).toEqual({ kind: 'forbidden', status: 403, code: 'forbidden' });
		expect((failure as ApiFailure).message).toBe('Команда запрещена: такие не отправляются никогда');
		expect(h.refreshCsrf).not.toHaveBeenCalled();
		expect(fetch.calls).toHaveLength(1);
	});

	it('409 confirm_required и сеть', async () => {
		const detail = {
			code: 'confirm_required',
			reason: 'missing',
			confirm_token: 't',
			expires_at: '2026-09-27T20:00:00Z',
			state_version: 1,
			command_class: 'risky'
		};
		const api = createApi(hooks(), mockFetch(() => json({ detail }, 409)));
		await expect(
			call(api.POST('/api/v1/commands/send', { body: { text: '/sell', idempotency_key: 'k' } }))
		).rejects.toMatchObject({ error: { kind: 'confirm', confirm: detail } });
		const offline = createApi(hooks(), (() => Promise.reject(new TypeError('fail'))) as typeof fetch);
		await expect(call(offline.GET('/api/v1/state'))).rejects.toMatchObject({
			error: { kind: 'network' }
		});
	});

	it('204 — успех без тела', async () => {
		const api = createApi(hooks(), mockFetch(() => new Response(null, { status: 204 })));
		await expect(call(api.POST('/api/v1/engine/resume'))).resolves.toBeUndefined();
	});
});
