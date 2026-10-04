import { cleanup, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type {
	AdminAccountOut,
	AdminAuditPageOut,
	AdminInviteCreatedOut,
	AdminInviteOut,
	AdminNotificationOut,
	AdminServerSettingsOut,
	AdminUserOut
} from '$lib/api/types';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { AdminStore } from '$lib/admin/store.svelte';
import AdminView from './AdminView.svelte';
import UsersTab from './UsersTab.svelte';
import AccountsTab from './AccountsTab.svelte';
import InvitesTab from './InvitesTab.svelte';
import ServerTab from './ServerTab.svelte';
import AuditTab from './AuditTab.svelte';
import ServerNotificationsTab from './ServerNotificationsTab.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));

const hooks = { csrf: () => 'csrf-test-token', refreshCsrf: async () => null, unauthorized: () => {} };

const testUser = (id: number, login: string, over: Partial<AdminUserOut> = {}): AdminUserOut => ({
	id,
	login,
	role: 'user',
	created_at: '2026-10-01T12:00:00Z',
	last_login_at: '2026-10-04T10:00:00Z',
	accounts: 2,
	max_accounts: 5,
	disabled: false,
	disabled_reason: null,
	deleting: false,
	...over
});

const testAccount = (id: number, name: string, over: Partial<AdminAccountOut> = {}): AdminAccountOut => ({
	id,
	name,
	owner_id: 1,
	owner_login: 'fenicu',
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	running: true,
	tg_online: true,
	restarts_24h: 0,
	last_error_code: null,
	last_error_at: null,
	messages_1h: 12,
	actions_1h: 4,
	rows: { messages: 100, decisions: 50 },
	...over
});

const testInvite = (id: number, tokenSuffix: string, over: Partial<AdminInviteOut> = {}): AdminInviteOut => ({
	id,
	created_at: '2026-10-04T12:00:00Z',
	expires_at: '2026-10-07T12:00:00Z',
	max_accounts: 3,
	note: 'Коллега',
	expired: false,
	...over
});

const testServerSettings = (over: Partial<AdminServerSettingsOut> = {}): AdminServerSettingsOut => ({
	version: 1,
	values: {
		retention: { messages_days: 90, decisions_days: 30, metrics_days: 365, ledger_days: 31, audit_days: 365 },
		invites: { default_ttl_h: 72, default_max_accounts: 1 },
		limits: { max_accounts_total: 50, sse_per_user: 5, tg_codes_per_hour: 10, tg_codes_per_account_hour: 3 },
		engine_bounds: {
			min_request_interval_s_min: 1.6,
			antiflood_pause_s_min: 10.0,
			antiflood_retry_max_max: 2,
			action_ttl_s_max: 600.0
		}
	},
	defaults: {
		retention: { messages_days: 90, decisions_days: 30, metrics_days: 365, ledger_days: 31, audit_days: 365 },
		invites: { default_ttl_h: 72, default_max_accounts: 1 },
		limits: { max_accounts_total: 50, sse_per_user: 5, tg_codes_per_hour: 10, tg_codes_per_account_hour: 3 },
		engine_bounds: {
			min_request_interval_s_min: 1.6,
			antiflood_pause_s_min: 10.0,
			antiflood_retry_max_max: 2,
			action_ttl_s_max: 600.0
		}
	},
	schema: {},
	...over
});

afterEach(() => {
	cleanup();
	vi.restoreAllMocks();
});

describe('Консоль владельца', () => {
	it('404 от консоли — текст для не-владельца', async () => {
		const fetch = mockFetch((c) => {
			if (c.url.startsWith('/api/v1/admin/')) {
				return json({ detail: 'not found' }, 404);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);

		// Render with role 'user'
		render(AdminView, { store, role: 'user', initialTab: 'users' });
		expect(await screen.findByText('Раздел только для владельца сервера')).toBeInTheDocument();
		const link = screen.getByRole('link', { name: /главную/i });
		expect(link).toHaveAttribute('href', '/');
	});

	it('удаление учётки требует точного логина', async () => {
		const usersList = [
			testUser(1, 'owner_user', { role: 'owner' }),
			testUser(2, 'alice', { role: 'user' })
		];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/users') {
				return json(usersList);
			}
			if (c.method === 'DELETE' && c.url === '/api/v1/admin/users/2') {
				return new Response(null, { status: 202 });
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadUsers();

		const user = userEvent.setup();
		render(UsersTab, { store });

		expect(await screen.findByText('alice')).toBeInTheDocument();
		const aliceRow = screen.getByTestId('user-row-2');
		const deleteBtn = within(aliceRow).getByRole('button', { name: /удалить/i });
		await user.click(deleteBtn);

		const dialog = screen.getByRole('dialog');
		const confirmBtn = within(dialog).getByRole('button', { name: /удалить/i });
		const input = within(dialog).getByLabelText(/логин/i);

		expect(confirmBtn).toBeDisabled();

		await user.type(input, 'ali');
		expect(confirmBtn).toBeDisabled();

		await user.clear(input);
		await user.type(input, 'alice');
		expect(confirmBtn).toBeEnabled();

		await user.click(confirmBtn);

		await waitFor(() => {
			const deleteCall = fetch.calls.find((c) => c.method === 'DELETE');
			expect(deleteCall).toBeDefined();
			expect(deleteCall?.url).toBe('/api/v1/admin/users/2');
			expect(JSON.parse(deleteCall?.body ?? '{}')).toEqual({ confirm_login: 'alice' });
		});
	});

	it('блокировка без причины не отправляется', async () => {
		const accountsList = [testAccount(1, 'acc-one')];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/accounts') {
				return json(accountsList);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/accounts/1') {
				return json(testAccount(1, 'acc-one', { blocked: true, blocked_reason: 'Спам' }));
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadAccounts();

		const user = userEvent.setup();
		render(AccountsTab, { store });

		expect(await screen.findByText('acc-one')).toBeInTheDocument();
		const accRow = screen.getByTestId('account-row-1');
		const blockBtn = within(accRow).getByRole('button', { name: /заблокировать/i });
		await user.click(blockBtn);

		const dialog = screen.getByRole('dialog');
		const submitBtn = within(dialog).getByRole('button', { name: /заблокировать/i });
		const reasonInput = within(dialog).getByLabelText(/причина/i);

		// With empty or whitespace reason, button must be disabled
		expect(submitBtn).toBeDisabled();

		await user.type(reasonInput, '   ');
		expect(submitBtn).toBeDisabled();

		// No PATCH call should have been made
		expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);

		// With valid reason, button enabled
		await user.clear(reasonInput);
		await user.type(reasonInput, 'Нарушение правил');
		expect(submitBtn).toBeEnabled();

		await user.click(submitBtn);

		await waitFor(() => {
			const patchCall = fetch.calls.find((c) => c.method === 'PATCH');
			expect(patchCall).toBeDefined();
			expect(patchCall?.url).toBe('/api/v1/admin/accounts/1');
			expect(JSON.parse(patchCall?.body ?? '{}')).toEqual({ blocked: true, reason: 'Нарушение правил' });
		});
	});

	it('ссылка приглашения показывается один раз', async () => {
		const invitesList: AdminInviteOut[] = [];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/invites') {
				return json(invitesList);
			}
			if (c.method === 'POST' && c.url === '/api/v1/admin/invites') {
				const created: AdminInviteCreatedOut = {
					invite: testInvite(10, 'secret_token_123', { max_accounts: 3, note: 'Друг' }),
					token: 'secret_token_123',
					path: '/invite/secret_token_123'
				};
				invitesList.push(created.invite);
				return json(created, 201);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadInvites();

		const user = userEvent.setup();
		render(InvitesTab, { store });

		const maxAccInput = screen.getByLabelText(/лимит аккаунтов/i);
		const noteInput = screen.getByLabelText(/пометка/i);
		const createBtn = screen.getByRole('button', { name: /создать приглашение/i });

		await user.type(maxAccInput, '3');
		await user.type(noteInput, 'Друг');
		await user.click(createBtn);

		// The created token link must appear with copy button
		const tokenLink = await screen.findByText(new RegExp('/invite/secret_token_123'));
		expect(tokenLink).toBeInTheDocument();
		const copyBtn = screen.getByRole('button', { name: /скопировать/i });
		expect(copyBtn).toBeInTheDocument();

		// Dismiss the one-time link display
		const dismissBtn = screen.getByRole('button', { name: /закрыть|понятно|готово/i });
		await user.click(dismissBtn);

		// Token link should disappear
		expect(screen.queryByText(new RegExp('/invite/secret_token_123'))).toBeNull();
		// In the list of invites, only the note/id/limit is shown, token is NOT shown
		expect(screen.getByText('Друг')).toBeInTheDocument();
	});

	it('настройки сервера: конфликт версии — перечитать', async () => {
		let settings = testServerSettings({ version: 1 });
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/server-settings') {
				return json(settings);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/server-settings') {
				return json({ detail: { code: 'version_conflict', version: 2 } }, 409);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadServerSettings();

		const user = userEvent.setup();
		render(ServerTab, { store });

		expect(await screen.findByDisplayValue('90')).toBeInTheDocument();
		const saveBtn = screen.getByRole('button', { name: /сохранить/i });
		await user.click(saveBtn);

		// Conflict message must appear
		expect(await screen.findByText(/настройки изменились, перечитать/i)).toBeInTheDocument();
		const reloadBtn = screen.getByRole('button', { name: /перечитать/i });

		// Update server version to 2 for subsequent load
		settings = testServerSettings({ version: 2 });
		await user.click(reloadBtn);

		await waitFor(() => {
			const getCalls = fetch.calls.filter((c) => c.method === 'GET' && c.url === '/api/v1/admin/server-settings');
			expect(getCalls.length).toBeGreaterThanOrEqual(2);
		});
	});

	it('изменение лимита аккаунтов пользователя', async () => {
		const usersList = [testUser(1, 'alice', { max_accounts: 5 })];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/users') {
				return json(usersList);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/users/1') {
				return json(testUser(1, 'alice', { max_accounts: 10 }));
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadUsers();

		const user = userEvent.setup();
		render(UsersTab, { store });

		const editBtn = screen.getByRole('button', { name: /лимит/i });
		await user.click(editBtn);

		const dialog = screen.getByRole('dialog');
		const input = within(dialog).getByLabelText(/максимум аккаунтов/i);
		await user.clear(input);
		await user.type(input, '10');

		const saveBtn = within(dialog).getByRole('button', { name: /сохранить/i });
		await user.click(saveBtn);

		await waitFor(() => {
			const patchCall = fetch.calls.find((c) => c.method === 'PATCH');
			expect(patchCall).toBeDefined();
			expect(JSON.parse(patchCall?.body ?? '{}')).toEqual({ max_accounts: 10 });
		});
	});

	it('ошибка last_owner при отключении последнего владельца показывается по-русски', async () => {
		const usersList = [testUser(1, 'root', { role: 'owner' })];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/users') {
				return json(usersList);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/users/1') {
				return json({ detail: 'last_owner' }, 409);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadUsers();

		const user = userEvent.setup();
		render(UsersTab, { store });

		const disableBtn = screen.getByRole('button', { name: /отключить/i });
		await user.click(disableBtn);

		const dialog = screen.getByRole('dialog');
		const confirmDisableBtn = within(dialog).getByRole('button', { name: /отключить/i });
		await user.click(confirmDisableBtn);

		expect(await within(dialog).findByText(/нельзя отключить или удалить последнего владельца/i)).toBeInTheDocument();
	});

	it('разблокировка и перезапуск аккаунта', async () => {
		const accountsList = [testAccount(1, 'blocked-acc', { blocked: true, running: true })];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/accounts') {
				return json(accountsList);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/accounts/1') {
				return json(testAccount(1, 'blocked-acc', { blocked: false }));
			}
			if (c.method === 'POST' && c.url === '/api/v1/admin/accounts/1/restart') {
				return new Response(null, { status: 202 });
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadAccounts();

		const user = userEvent.setup();
		render(AccountsTab, { store });

		const unblockBtn = screen.getByRole('button', { name: /разблокировать/i });
		await user.click(unblockBtn);

		await waitFor(() => {
			const patchCall = fetch.calls.find((c) => c.method === 'PATCH');
			expect(patchCall).toBeDefined();
			expect(JSON.parse(patchCall?.body ?? '{}')).toEqual({ blocked: false, reason: null });
		});

		const restartBtn = screen.getByRole('button', { name: /перезапустить/i });
		await user.click(restartBtn);

		await waitFor(() => {
			const postCall = fetch.calls.find((c) => c.method === 'POST');
			expect(postCall).toBeDefined();
			expect(postCall?.url).toBe('/api/v1/admin/accounts/1/restart');
		});
	});

	it('отзыв приглашения', async () => {
		const invitesList = [testInvite(1, 'token1', { note: 'Коллега' })];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/invites') {
				return json(invitesList);
			}
			if (c.method === 'DELETE' && c.url === '/api/v1/admin/invites/1') {
				return new Response(null, { status: 204 });
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadInvites();

		const user = userEvent.setup();
		render(InvitesTab, { store });

		const revokeBtn = screen.getByRole('button', { name: /отозвать/i });
		await user.click(revokeBtn);

		await waitFor(() => {
			const delCall = fetch.calls.find((c) => c.method === 'DELETE');
			expect(delCall).toBeDefined();
			expect(delCall?.url).toBe('/api/v1/admin/invites/1');
		});
	});

	it('журнал действий и пагинация Ещё', async () => {
		const page1: AdminAuditPageOut = {
			items: [
				{
					id: 10,
					at: '2026-10-04T12:00:00Z',
					actor_login: 'fenicu',
					actor_user_id: 1,
					action: 'account_blocked',
					target_type: 'account',
					target_id: 2,
					details: { reason: 'test' }
				}
			],
			next_before: 10
		};
		const page2: AdminAuditPageOut = {
			items: [
				{
					id: 9,
					at: '2026-10-04T11:00:00Z',
					actor_login: 'fenicu',
					actor_user_id: 1,
					action: 'user_created',
					target_type: 'user',
					target_id: 3,
					details: {}
				}
			],
			next_before: null
		};

		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/audit?limit=50') {
				return json(page1);
			}
			if (c.method === 'GET' && c.url === '/api/v1/admin/audit?limit=50&before=10') {
				return json(page2);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);

		const user = userEvent.setup();
		render(AuditTab, { store });

		expect(await screen.findByText('account_blocked')).toBeInTheDocument();
		const moreBtn = screen.getByRole('button', { name: /ещё/i });
		await user.click(moreBtn);

		expect(await screen.findByText('user_created')).toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /ещё/i })).toBeNull();
	});

	it('уведомления сервера и отметка всех прочитанными', async () => {
		const notifications: AdminNotificationOut[] = [
			{
				id: 1,
				created_at: '2026-10-04T12:00:00Z',
				level: 'warn',
				code: 'tg_codes_limit',
				text: 'Лимит кодов Telegram исчерпан',
				read: false
			}
		];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/notifications?limit=50') {
				return json(notifications);
			}
			if (c.method === 'POST' && c.url === '/api/v1/admin/notifications/read') {
				return new Response(null, { status: 204 });
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);

		const user = userEvent.setup();
		render(ServerNotificationsTab, { store });

		expect(await screen.findByText('tg_codes_limit')).toBeInTheDocument();
		const markAllBtn = screen.getByRole('button', { name: /отметить все прочитанными/i });
		expect(markAllBtn).toBeEnabled();

		await user.click(markAllBtn);

		await waitFor(() => {
			const readCall = fetch.calls.find((c) => c.method === 'POST' && c.url === '/api/v1/admin/notifications/read');
			expect(readCall).toBeDefined();
			expect(JSON.parse(readCall?.body ?? '{}')).toEqual({ up_to_id: 1 });
		});
	});

	it('переключение вкладок в AdminView', async () => {
		const fetch = mockFetch((c) => json([], 200));
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);

		const user = userEvent.setup();
		render(AdminView, { store, role: 'owner', initialTab: 'users' });

		expect(screen.getByRole('button', { name: /пользователи/i })).toHaveAttribute('aria-current', 'page');

		const accountsTabBtn = screen.getByRole('button', { name: /аккаунты/i });
		await user.click(accountsTabBtn);

		expect(accountsTabBtn).toHaveAttribute('aria-current', 'page');
	});
});

