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
import { ApiFailure } from '$lib/api/errors';
import { AdminStore, formatAdminError } from '$lib/admin/store.svelte';
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
	it('404 от консоли — store.forbidden для владельца', async () => {
		const fetch = mockFetch((c) => {
			if (c.url.startsWith('/api/v1/admin/users')) {
				return json({ detail: 'not found' }, 404);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);

		render(AdminView, { store, role: 'owner', initialTab: 'users' });
		expect(await screen.findByText('Раздел только для владельца сервера')).toBeInTheDocument();
		expect(store.forbidden).toBe(true);
		const link = screen.getByRole('link', { name: /главную/i });
		expect(link).toHaveAttribute('href', '/');
	});

	it('не-владелец не делает ни одного запроса к /admin/*', async () => {
		const fetch = mockFetch(() => json({}, 200));
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);

		render(AdminView, { store, role: 'user', initialTab: 'users' });
		expect(await screen.findByText('Раздел только для владельца сервера')).toBeInTheDocument();
		const adminCalls = fetch.calls.filter((c) => c.url.startsWith('/api/v1/admin/'));
		expect(adminCalls).toHaveLength(0);
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
			expect(deleteCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
			expect(JSON.parse(deleteCall?.body ?? '{}')).toEqual({ confirm_login: 'alice' });
		});
	});

	it('отключение учётки: проверка тела PATCH и X-CSRF-Token', async () => {
		const usersList = [
			testUser(1, 'owner_user', { role: 'owner' }),
			testUser(2, 'bob', { role: 'user' })
		];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/users') {
				return json(usersList);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/users/2') {
				return json(testUser(2, 'bob', { disabled: true, disabled_reason: 'Спам' }));
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadUsers();

		const user = userEvent.setup();
		render(UsersTab, { store });

		expect(await screen.findByText('bob')).toBeInTheDocument();
		const bobRow = screen.getByTestId('user-row-2');
		const disableBtn = within(bobRow).getByRole('button', { name: /отключить/i });
		await user.click(disableBtn);

		const dialog = screen.getByRole('dialog');
		const reasonInput = within(dialog).getByLabelText(/причина/i);
		await user.type(reasonInput, 'Спам');

		const confirmBtn = within(dialog).getByRole('button', { name: /отключить/i });
		await user.click(confirmBtn);

		await waitFor(() => {
			const patchCall = fetch.calls.find((c) => c.method === 'PATCH' && c.url === '/api/v1/admin/users/2');
			expect(patchCall).toBeDefined();
			expect(patchCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
			expect(JSON.parse(patchCall?.body ?? '{}')).toEqual({ disabled: true, reason: 'Спам' });
		});
	});

	it('удаление аккаунта по точному имени', async () => {
		const accountsList = [testAccount(1, 'acc-one')];
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/accounts') {
				return json(accountsList);
			}
			if (c.method === 'DELETE' && c.url === '/api/v1/admin/accounts/1') {
				return new Response(null, { status: 202 });
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
		const deleteBtn = within(accRow).getByRole('button', { name: /удалить/i });
		await user.click(deleteBtn);

		const dialog = screen.getByRole('dialog');
		const confirmBtn = within(dialog).getByRole('button', { name: /удалить навсегда/i });
		const input = within(dialog).getByPlaceholderText('acc-one');

		expect(confirmBtn).toBeDisabled();

		await user.type(input, 'acc-two');
		expect(confirmBtn).toBeDisabled();

		await user.clear(input);
		await user.type(input, 'acc-one');
		expect(confirmBtn).toBeEnabled();

		await user.click(confirmBtn);

		await waitFor(() => {
			const deleteCall = fetch.calls.find((c) => c.method === 'DELETE' && c.url === '/api/v1/admin/accounts/1');
			expect(deleteCall).toBeDefined();
			expect(deleteCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
			expect(JSON.parse(deleteCall?.body ?? '{}')).toEqual({ confirm_name: 'acc-one' });
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

	it('создание приглашения: проверка тела POST, полного URL ссылки и X-CSRF-Token', async () => {
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
		const ttlInput = screen.getByLabelText(/срок в часах/i);
		const noteInput = screen.getByLabelText(/пометка/i);
		const createBtn = screen.getByRole('button', { name: /создать приглашение/i });

		await user.type(maxAccInput, '3');
		await user.type(ttlInput, '48');
		await user.type(noteInput, 'Друг');
		await user.click(createBtn);

		await waitFor(() => {
			const postCall = fetch.calls.find((c) => c.method === 'POST' && c.url === '/api/v1/admin/invites');
			expect(postCall).toBeDefined();
			expect(postCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
			expect(JSON.parse(postCall?.body ?? '{}')).toEqual({
				max_accounts: 3,
				ttl_h: 48,
				note: 'Друг'
			});
		});

		// The created token link must appear with full URL (location.origin + path)
		const expectedUrl = `${location.origin}/invite/secret_token_123`;
		const tokenLink = await screen.findByDisplayValue(expectedUrl);
		expect(tokenLink).toBeInTheDocument();
		const copyBtn = screen.getByRole('button', { name: /скопировать/i });
		expect(copyBtn).toBeInTheDocument();

		// Dismiss the one-time link display
		const dismissBtn = screen.getByRole('button', { name: /закрыть|понятно|готово/i });
		await user.click(dismissBtn);

		// Token link should disappear
		expect(screen.queryByDisplayValue(expectedUrl)).toBeNull();
		// In the list of invites, note is shown
		expect(screen.getByText('Друг')).toBeInTheDocument();
	});

	it('создание приглашения: дробный лимит (2.5) отклоняется клиентом', async () => {
		const fetch = mockFetch(() => json([], 200));
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadInvites();

		const user = userEvent.setup();
		render(InvitesTab, { store });

		const maxAccInput = screen.getByLabelText(/лимит аккаунтов/i);
		const createBtn = screen.getByRole('button', { name: /создать приглашение/i });

		await user.type(maxAccInput, '2.5');
		await user.click(createBtn);

		expect(await screen.findByText('Лимит аккаунтов должен быть целым числом от 1 до 1000')).toBeInTheDocument();
		expect(fetch.calls.filter((c) => c.method === 'POST')).toHaveLength(0);
	});

	it('настройки сервера: успешный PATCH с version, changes и X-CSRF-Token', async () => {
		const settings = testServerSettings({ version: 1 });
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/server-settings') {
				return json(settings);
			}
			if (c.method === 'PATCH' && c.url === '/api/v1/admin/server-settings') {
				return json({
					version: 2,
					values: { ...settings.values, retention: { ...(settings.values as Record<string, any>).retention, messages_days: 100 } },
					changed: ['retention.messages_days']
				});
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadServerSettings();

		const user = userEvent.setup();
		render(ServerTab, { store });

		const msgInput = await screen.findByDisplayValue('90');
		await user.clear(msgInput);
		await user.type(msgInput, '100');

		const saveBtn = screen.getByRole('button', { name: /сохранить настройки/i });
		await user.click(saveBtn);

		await waitFor(() => {
			const patchCall = fetch.calls.find((c) => c.method === 'PATCH' && c.url === '/api/v1/admin/server-settings');
			expect(patchCall).toBeDefined();
			expect(patchCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
			const body = JSON.parse(patchCall?.body ?? '{}');
			expect(body.version).toBe(1);
			expect(body.changes?.retention?.messages_days).toBe(100);
		});

		expect(await screen.findByText('Версия: 2')).toBeInTheDocument();
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

		await waitFor(() => {
			const patchCall = fetch.calls.find((c) => c.method === 'PATCH' && c.url === '/api/v1/admin/server-settings');
			expect(patchCall).toBeDefined();
			expect(patchCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
			const body = JSON.parse(patchCall?.body ?? '{}');
			expect(body.version).toBe(1);
			expect(body.changes).toBeDefined();
		});

		// Conflict message must appear with version 2
		expect(await screen.findByText(/настройки изменились, перечитать/i)).toBeInTheDocument();
		expect(screen.getByText(/\(версия на сервере: 2\)/i)).toBeInTheDocument();
		const reloadBtn = screen.getByRole('button', { name: /перечитать/i });

		// Update server version to 2 for subsequent load
		settings = testServerSettings({ version: 2 });
		await user.click(reloadBtn);

		await waitFor(() => {
			const getCalls = fetch.calls.filter((c) => c.method === 'GET' && c.url === '/api/v1/admin/server-settings');
			expect(getCalls.length).toBeGreaterThanOrEqual(2);
		});

		// After reload, conflict banner disappears and version is updated
		expect(screen.queryByText(/настройки изменились, перечитать/i)).toBeNull();
		expect(screen.getByText('Версия: 2')).toBeInTheDocument();
	});

	it('настройки сервера: валидация берет границы из schema (messages_days minimum: 10)', async () => {
		const settingsWithSchema = testServerSettings({
			schema: {
				$defs: {
					RetentionPolicy: {
						properties: {
							messages_days: {
								type: 'integer',
								minimum: 10,
								maximum: 3650
							}
						}
					}
				}
			}
		});
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/server-settings') {
				return json(settingsWithSchema);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadServerSettings();

		const user = userEvent.setup();
		render(ServerTab, { store });

		const msgInput = await screen.findByDisplayValue('90');
		await user.clear(msgInput);
		await user.type(msgInput, '5');

		const saveBtn = screen.getByRole('button', { name: /сохранить настройки/i });
		await user.click(saveBtn);

		expect(await screen.findByRole('alert')).toHaveTextContent(/10/);
		expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
	});

	it('настройки сервера: пустые и дробные значения отклоняются на клиенте', async () => {
		const settings = testServerSettings();
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/server-settings') {
				return json(settings);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadServerSettings();

		const user = userEvent.setup();
		render(ServerTab, { store });

		const msgInput = await screen.findByDisplayValue('90');
		const saveBtn = screen.getByRole('button', { name: /сохранить настройки/i });

		// 1. Cleared input (empty / null)
		await user.clear(msgInput);
		await user.click(saveBtn);
		expect(await screen.findByRole('alert')).toHaveTextContent(/Хранение сообщений/i);
		expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);

		// 2. Fractional value (1.5 in integer field)
		await user.type(msgInput, '1.5');
		await user.click(saveBtn);
		expect(await screen.findByRole('alert')).toHaveTextContent(/целым/i);
		expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
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

	it('пользователи: дробный лимит учётки (2.5) отклоняется клиентом', async () => {
		const usersList = [testUser(1, 'alice', { max_accounts: 5 })];
		const fetch = mockFetch(() => json(usersList, 200));
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
		await user.type(input, '2.5');

		const saveBtn = within(dialog).getByRole('button', { name: /сохранить/i });
		await user.click(saveBtn);

		expect(await within(dialog).findByText('Лимит должен быть целым числом от 1 до 1000')).toBeInTheDocument();
		expect(fetch.calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
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
			expect(delCall?.headers.get('x-csrf-token')).toBe('csrf-test-token');
		});
	});

	it('отзыв приглашения с 410 invite_gone: показ русского текста и повторный запрос списка', async () => {
		const invitesList = [testInvite(1, 'token1', { note: 'Коллега' })];
		let getCallsCount = 0;
		const fetch = mockFetch((c) => {
			if (c.method === 'GET' && c.url === '/api/v1/admin/invites') {
				getCallsCount++;
				return json(invitesList);
			}
			if (c.method === 'DELETE' && c.url === '/api/v1/admin/invites/1') {
				return json({ detail: 'invite_gone' }, 410);
			}
			return json({}, 200);
		});
		const api = createApi(hooks, fetch);
		const store = new AdminStore(api);
		await store.loadInvites();
		expect(getCallsCount).toBe(1);

		const user = userEvent.setup();
		render(InvitesTab, { store });

		const revokeBtn = screen.getByRole('button', { name: /отозвать/i });
		await user.click(revokeBtn);

		await waitFor(() => {
			// Reload of invites must be triggered after 410 so stale invite disappears
			expect(getCallsCount).toBeGreaterThanOrEqual(2);
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

	it('русские тексты ошибок 422 в formatAdminError', () => {
		const fail1 = new ApiFailure({
			kind: 'validation',
			status: 422,
			issues: [
				{
					loc: ['body', 'changes', 'retention', 'messages_days'],
					msg: 'Input should be a valid integer, unable to parse string as an integer',
					type: 'int_parsing'
				}
			]
		});
		expect(formatAdminError(fail1)).toBe('Хранение сообщений: целое число');

		const fail2 = new ApiFailure({
			kind: 'validation',
			status: 422,
			issues: [
				{
					loc: ['body', 'changes', 'retention', 'ledger_days'],
					msg: 'Input should be greater than or equal to 31',
					type: 'greater_than_equal'
				}
			]
		});
		expect(formatAdminError(fail2)).toBe('Хранение прихода (ledger): не меньше 31');

		const fail3 = new ApiFailure({
			kind: 'validation',
			status: 422,
			issues: [
				{
					loc: ['body', 'changes', 'engine_bounds', 'action_ttl_s_max'],
					msg: 'Input should be greater than 0',
					type: 'greater_than'
				}
			]
		});
		expect(formatAdminError(fail3)).toBe('Макс. срок действия: больше 0');

		const fail4 = new ApiFailure({
			kind: 'validation',
			status: 422,
			issues: [
				{
					loc: ['body', 'changes', 'limits', 'max_accounts_total'],
					msg: 'Input should be less than or equal to 10000',
					type: 'less_than_equal'
				}
			]
		});
		expect(formatAdminError(fail4)).toBe('Максимум аккаунтов на сервере: не больше 10000');
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

