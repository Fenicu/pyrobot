import { afterEach, describe, expect, it, vi } from 'vitest';
import type { AccountOut } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { FakeSource } from '$lib/test/source';
import { accounts, current, startAccount } from './app.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const account = (id: number, status: AccountOut['status'] = 'enabled'): AccountOut => ({
	id,
	name: `acc${id}`,
	status,
	status_reason: null,
	tg: { user_id: id, online: true },
	mode: 'dry_run',
	paused: false,
	killed: false,
	last_action_at: null,
	unread: { warn: 0, error: 0 },
	company: null,
	team_tag: null
});

const state = { list: [account(1), account(2)], listFails: false };
const sources: FakeSource[] = [];
const fetch = mockFetch((c) => {
	if (c.url === '/api/v1/accounts') return state.listFails ? json({ detail: 'x' }, 500) : json(state.list);
	return json({ detail: 'engine not running' }, 503);
});

// Синглтоны вкладки — настоящие, на подделках fetch и EventSource.
vi.stubGlobal('fetch', fetch);
vi.stubGlobal(
	'EventSource',
	class extends FakeSource {
		constructor(url: string) {
			super(url);
			sources.push(this);
		}
	}
);

afterEach(() => {
	current.stop();
	accounts.stop();
	sources.length = 0;
	state.list = [account(1), account(2)];
	state.listFails = false;
});

describe('открытый аккаунт и список аккаунтов', () => {
	it('аккаунта больше нет в списке (удалён, в том числе в другой вкладке) — контекст остановлен', async () => {
		await accounts.load();
		const ctx = startAccount(2);
		await vi.waitFor(() => expect(sources).toHaveLength(1));
		expect(current.ctx).toBe(ctx);

		state.list = [account(1)];
		await accounts.load();
		expect(current.ctx).toBeNull();
		expect(sources[0]!.closed).toBe(true);
		expect(ctx.live.status).toBe('idle');
	});

	it('аккаунт в списке (в том числе удаляемый), список не загружен или не прочитан — контекст остаётся', async () => {
		startAccount(2);
		expect(current.ctx?.id).toBe(2);

		state.list = [account(1), account(2, 'deleting')];
		await accounts.load();
		expect(current.ctx?.id).toBe(2);

		state.listFails = true;
		state.list = [];
		await accounts.load();
		expect(accounts.error).not.toBeNull();
		expect(current.ctx?.id).toBe(2);
		expect(sources[0]!.closed).toBe(false);
	});
});
