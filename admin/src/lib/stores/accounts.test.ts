import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { AccountOut } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { ACCOUNTS_POLL_MS, AccountsStore } from './accounts.svelte';

const account = (id: number, name: string): AccountOut => ({
	id,
	name,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: 100 + id, online: true },
	mode: 'dry_run',
	paused: false,
	killed: false,
	last_action_at: null,
	unread: { warn: 0, error: 0 },
	company: null,
	team_tag: null
});

let visibility: DocumentVisibilityState = 'visible';
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => visibility });

function show(state: DocumentVisibilityState) {
	visibility = state;
	document.dispatchEvent(new Event('visibilitychange'));
}

afterEach(() => {
	vi.useRealTimers();
	visibility = 'visible';
});

describe('список аккаунтов', () => {
	it('опрос раз в 30 с и при возвращении на вкладку', async () => {
		vi.useFakeTimers();
		const lists = [[account(1, 'main')], [account(1, 'main'), account(2, 'twink')]];
		const fetch = mockFetch(() => json(lists.shift() ?? []));
		const store = new AccountsStore(
			createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch)
		);
		expect(store.list).toBeNull();
		store.start();
		await vi.advanceTimersByTimeAsync(0);
		expect(fetch.calls.map((c) => c.url)).toEqual(['/api/v1/accounts']);
		expect(store.list?.map((a) => a.name)).toEqual(['main']);

		await vi.advanceTimersByTimeAsync(ACCOUNTS_POLL_MS - 1);
		expect(fetch.calls).toHaveLength(1);
		await vi.advanceTimersByTimeAsync(1);
		expect(fetch.calls).toHaveLength(2);
		expect(store.list?.map((a) => a.name)).toEqual(['main', 'twink']);

		// Ушли со вкладки — не перечитываем; вернулись — сразу.
		show('hidden');
		await vi.advanceTimersByTimeAsync(0);
		expect(fetch.calls).toHaveLength(2);
		show('visible');
		await vi.advanceTimersByTimeAsync(0);
		expect(fetch.calls).toHaveLength(3);

		store.stop();
		show('hidden');
		show('visible');
		await vi.advanceTimersByTimeAsync(ACCOUNTS_POLL_MS * 2);
		expect(fetch.calls).toHaveLength(3);
	});
});
