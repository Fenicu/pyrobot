import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SessionHooks } from '$lib/api/client';
import type { EngineStatus, StateOut } from '$lib/api/types';
import { ENGINE_POLL_MS } from '$lib/stores/engine.svelte';
import { STATE_RELOAD_MS } from '$lib/stores/character.svelte';
import { deferred } from '$lib/test/deferred';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { FakeSource } from '$lib/test/source';
import { AccountContext, CurrentAccount } from './account.svelte';

const hooks: SessionHooks = { csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} };

// Аккаунты различимы по ответам: у первого режим live и деньги 1, у второго — dry_run и 47.
const status = (id: number): EngineStatus => ({
	...fixture<EngineStatus>('engine_status'),
	mode: id === 1 ? 'live' : 'dry_run'
});
const snap = (version: number, money: number): StateOut => ({
	version,
	now: '2026-09-27T20:00:00Z',
	state: { money: { value: money, at: '2026-09-27T20:00:00Z', src: 'screen' } } as StateOut['state'],
	stale: []
});

function answer(c: Call): Response {
	const m = /^\/api\/v1\/accounts\/(\d+)(\/[^?]*)/.exec(c.url);
	const id = Number(m?.[1]);
	if (m?.[2] === '/engine/status') return json(status(id));
	if (m?.[2] === '/state') return json(id === 1 ? snap(638, 1) : snap(5, 47));
	if (m?.[2] === '/notifications') return json({ items: [], unread: 0, unread_alerts: id, next_before: null });
	return json({ detail: 'Not Found' }, 404);
}

function rig(handler: (c: Call) => Response | Promise<Response> = answer) {
	const sources: FakeSource[] = [];
	const fetch = mockFetch(handler);
	const current = new CurrentAccount(
		(id) =>
			new AccountContext(id, {
				hooks,
				fetch,
				createSource: (url) => {
					const s = new FakeSource(url);
					sources.push(s);
					return s;
				},
				checkSession: async () => 'ok',
				onUnauthorized: () => {}
			})
	);
	const calls = (id: number) => fetch.calls.filter((c) => c.url.startsWith(`/api/v1/accounts/${id}/`)).length;
	return { current, sources, fetch, calls };
}

const path = (url: string) => new URL(url, 'http://app.invalid').pathname;
const settle = () => vi.advanceTimersByTimeAsync(0);
const warn = (id: number) => JSON.stringify({ id, level: 'warn', code: 'x', text: 'x' });

afterEach(() => vi.useRealTimers());

describe('контекст аккаунта', () => {
	it('переключение останавливает SSE прежнего аккаунта и открывает новый', async () => {
		vi.useFakeTimers();
		const r = rig();
		const one = r.current.start(1);
		await settle();
		expect(r.sources.map((s) => path(s.url))).toEqual(['/api/v1/accounts/1/events']);
		expect(one.unread.count).toBe(1);

		const two = r.current.start(2);
		await settle();
		expect(r.current.ctx).toBe(two);
		expect(r.sources[0]!.closed).toBe(true);
		expect(one.live.status).toBe('idle');
		expect(r.sources.map((s) => path(s.url))).toEqual(['/api/v1/accounts/1/events', '/api/v1/accounts/2/events']);
		expect(two.unread.count).toBe(2);

		// Кадры нового потока доходят до хранилищ нового контекста, старого — никуда.
		r.sources[1]!.open();
		expect(two.live.status).toBe('open');
		r.sources[1]!.send('notification', warn(10));
		r.sources[0]!.send('notification', warn(11));
		expect([one.unread.count, two.unread.count]).toEqual([1, 3]);

		r.current.stop();
		expect(r.current.ctx).toBeNull();
		expect(r.sources[1]!.closed).toBe(true);
	});

	it('поздний ответ прежнего аккаунта не попадает в новый контекст', async () => {
		vi.useFakeTimers();
		const slow = deferred<Response>();
		const r = rig((c) => (c.url === '/api/v1/accounts/1/engine/status' ? slow.promise : answer(c)));
		const one = r.current.start(1);
		await settle();
		const two = r.current.start(2);
		await settle();
		expect(two.engine.status?.mode).toBe('dry_run');

		slow.resolve(json(status(1)));
		await settle();
		expect(r.current.ctx).toBe(two);
		expect(two.engine.status?.mode).toBe('dry_run');
		// Прежний контекст остановлен: ни потока, ни опросов статуса и состояния.
		expect(r.sources[0]!.closed).toBe(true);
		expect(one.live.status).toBe('idle');
		const before = [r.calls(1), r.calls(2)];
		await vi.advanceTimersByTimeAsync(Math.max(ENGINE_POLL_MS, STATE_RELOAD_MS));
		expect(r.calls(1)).toBe(before[0]);
		expect(r.calls(2)).toBeGreaterThan(before[1]!);
		r.current.stop();
	});

	it('CharacterStore нового аккаунта принимает снимок с меньшей версией', async () => {
		vi.useFakeTimers();
		const r = rig();
		const one = r.current.start(1);
		await settle();
		expect([one.character.version, one.character.state.money?.value]).toEqual([638, 1]);
		const two = r.current.start(2);
		await settle();
		expect([two.character.version, two.character.state.money?.value]).toEqual([5, 47]);
		r.current.stop();
	});
});
