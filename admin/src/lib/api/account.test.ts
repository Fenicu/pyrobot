import { describe, expect, it, vi } from 'vitest';
import { json, mockFetch } from '$lib/test/fetch';
import { createAccountApi, eventsUrl } from './account';
import { call, type SessionHooks } from './client';

const hooks = {
	csrf: vi.fn(() => 'tok-1'),
	refreshCsrf: vi.fn(async () => 'tok-2'),
	unauthorized: vi.fn()
} satisfies SessionHooks;

describe('клиент API аккаунта', () => {
	it('подставляет аккаунт в путь', async () => {
		const f = mockFetch(() => json({}));
		await call(createAccountApi(hooks, 7, f).GET('/engine/status'));
		expect(f.calls[0]?.url).toBe('/api/v1/accounts/7/engine/status');
		await call(
			createAccountApi(hooks, 7, f).GET('/decisions/{decision_id}', { params: { path: { decision_id: 3 } } })
		);
		expect(f.calls[1]?.url).toBe('/api/v1/accounts/7/decisions/3');
	});

	it('шлёт CSRF и повторяет после csrf token mismatch', async () => {
		const f = mockFetch((c) =>
			c.headers.get('X-CSRF-Token') === 'tok-2'
				? new Response(null, { status: 204 })
				: json({ detail: 'csrf token mismatch' }, 403)
		);
		await call(createAccountApi(hooks, 2, f).POST('/engine/kill', { body: { reason: 'r' } }));
		expect(f.calls.map((c) => [c.url, c.headers.get('X-CSRF-Token')])).toEqual([
			['/api/v1/accounts/2/engine/kill', 'tok-1'],
			['/api/v1/accounts/2/engine/kill', 'tok-2']
		]);
		expect(JSON.parse(f.calls[1]?.body ?? '')).toEqual({ reason: 'r' });
		expect(hooks.refreshCsrf).toHaveBeenCalledTimes(1);
	});

	it('eventsUrl', () => expect(eventsUrl(7)).toMatch(/\/api\/v1\/accounts\/7\/events$/));
});
