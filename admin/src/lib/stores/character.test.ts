import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAccountApi } from '$lib/api/account';
import type { StateOut } from '$lib/api/types';
import { deferred, flush, type Deferred } from '$lib/test/deferred';
import { json, mockFetch } from '$lib/test/fetch';
import { CharacterStore, STATE_RELOAD_MS } from './character.svelte';

const obs = (value: unknown) => ({ value, at: '2026-09-27T20:00:00Z', src: 'screen' });
const snap = (version: number, money: number): StateOut => ({
	version,
	now: '2026-09-27T20:00:00Z',
	state: { money: obs(money) } as StateOut['state'],
	stale: []
});

/** Каждый GET /state ждёт, пока тест не ответит на него сам. */
function store() {
	const pending: Deferred<StateOut>[] = [];
	const fetch = mockFetch(async () => {
		const d = deferred<StateOut>();
		pending.push(d);
		return json(await d.promise);
	});
	const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
	return { s: new CharacterStore(api), pending, fetch };
}

async function loaded(version: number, money: number) {
	const r = store();
	const first = r.s.load();
	await flush();
	r.pending[0]!.resolve(snap(version, money));
	await first;
	return r;
}

afterEach(() => vi.useRealTimers());

describe('состояние по версиям', () => {
	it('следующая версия — корневые поля заменяются, старая — пропуск', async () => {
		const { s } = await loaded(638, 1);
		s.apply({ version: 639, changed: { money: obs(47), busy: null } });
		expect(s.version).toBe(639);
		expect(s.state.money?.value).toBe(47);
		expect(s.state.busy).toBeNull();
		s.apply({ version: 639, changed: { money: obs(1) } });
		expect(s.state.money?.value).toBe(47);
	});

	it('поздний ответ /state со старой версией не откатывает кадр SSE', async () => {
		const { s, pending } = await loaded(638, 1);
		const again = s.load();
		await flush();
		s.apply({ version: 639, changed: { money: obs(47) } });
		pending[1]!.resolve(snap(638, 1));
		await again;
		expect([s.version, s.state.money?.value]).toEqual([639, 47]);
		// Свежий снимок той же или новой версии применяется.
		const third = s.load();
		await flush();
		pending[2]!.resolve(snap(640, 50));
		await third;
		expect([s.version, s.state.money?.value]).toEqual([640, 50]);
	});

	it('кадры, пришедшие до первого снимка, применяются после него', async () => {
		const { s, pending } = store();
		const first = s.load();
		await flush();
		s.apply({ version: 639, changed: { money: obs(47) } });
		s.apply({ version: 640, changed: { money: obs(48) } });
		pending[0]!.resolve(snap(638, 1));
		await first;
		expect([s.version, s.state.money?.value]).toEqual([640, 48]);
	});

	it('разрыв версий — новая синхронизация, кадр за снимком не теряется', async () => {
		const { s, pending, fetch } = await loaded(638, 1);
		s.apply({ version: 641, changed: { money: obs(5) } });
		await flush();
		expect(fetch.calls).toHaveLength(2);
		// Второй кадр во время синхронизации не запускает ещё одну.
		s.apply({ version: 642, changed: { money: obs(6) } });
		await flush();
		expect(fetch.calls).toHaveLength(2);
		pending[1]!.resolve(snap(641, 5));
		await flush();
		expect([s.version, s.state.money?.value]).toEqual([642, 6]);
	});

	it('снимок старше кадров разрыва — ещё одна синхронизация', async () => {
		const { s, pending, fetch } = await loaded(638, 1);
		s.apply({ version: 641, changed: { money: obs(5) } });
		await flush();
		pending[1]!.resolve(snap(639, 2));
		await flush();
		expect(fetch.calls).toHaveLength(3);
		pending[2]!.resolve(snap(641, 5));
		await flush();
		expect([s.version, s.state.money?.value]).toEqual([641, 5]);
	});

	it('сбой синхронизации не зацикливает запросы', async () => {
		const answers = [json(snap(638, 1)), json({ detail: 'engine not running' }, 503)];
		const fetch = mockFetch(() => answers.shift() ?? json({ detail: 'engine not running' }, 503));
		const api = createAccountApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, 1, fetch);
		const s = new CharacterStore(api);
		await s.load();
		s.apply({ version: 641, changed: { money: obs(5) } });
		for (let i = 0; i < 5; i++) await flush();
		expect(fetch.calls).toHaveLength(2);
		expect(s.error).toMatchObject({ kind: 'engine_down' });
		expect(s.version).toBe(638);
	});

	it('перечитывание раз в минуту', async () => {
		vi.useFakeTimers();
		const { s, pending, fetch } = store();
		s.start();
		await vi.advanceTimersByTimeAsync(0);
		pending[0]!.resolve(snap(1, 1));
		await vi.advanceTimersByTimeAsync(STATE_RELOAD_MS - 1);
		expect(fetch.calls).toHaveLength(1);
		await vi.advanceTimersByTimeAsync(1);
		expect(fetch.calls).toHaveLength(2);
		s.stop();
	});
});
