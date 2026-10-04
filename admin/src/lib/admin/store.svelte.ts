import { call, type Api } from '$lib/api/client';
import { ApiFailure, errorText } from '$lib/api/errors';
import type {
	AdminAccountDeleteIn,
	AdminAccountOut,
	AdminAccountPatchIn,
	AdminAuditOut,
	AdminAuditPageOut,
	AdminInviteCreateIn,
	AdminInviteCreatedOut,
	AdminInviteOut,
	AdminNotificationOut,
	AdminNotificationReadIn,
	AdminServerSettingsOut,
	AdminServerSettingsPatchIn,
	AdminServerSettingsPatchOut,
	AdminUserDeleteIn,
	AdminUserOut,
	AdminUserPatchIn
} from '$lib/api/types';

export function formatAdminError(e: unknown): string {
	if (e instanceof ApiFailure) {
		const err = e.error;
		if (err.kind === 'conflict' && err.code === 'last_owner') {
			return 'Нельзя отключить или удалить последнего владельца';
		}
		if (err.kind === 'validation') {
			for (const issue of err.issues) {
				const field = issue.loc.at(-1);
				if (field === 'max_accounts') return 'Лимит аккаунтов должен быть от 1 до 1000';
				if (field === 'ttl_h') return 'Срок приглашения должен быть от 1 до 720 часов';
				if (field === 'note') return 'Пометка не должна превышать 128 символов';
				if (field === 'reason') return 'Причина не должна превышать 256 символов';
				if (field === 'confirm_login') return 'Логин для подтверждения введён неверно';
				if (field === 'confirm_name') return 'Имя аккаунта для подтверждения введено неверно';
			}
		}
		return errorText(err);
	}
	return String(e);
}

export class AdminStore {
	readonly api: Api;
	forbidden = $state(false);

	// Пользователи
	users = $state<AdminUserOut[] | null>(null);
	usersError = $state('');
	usersLoading = $state(false);

	// Аккаунты
	accounts = $state<AdminAccountOut[] | null>(null);
	accountsError = $state('');
	accountsLoading = $state(false);

	// Приглашения
	invites = $state<AdminInviteOut[] | null>(null);
	invitesError = $state('');
	invitesLoading = $state(false);

	// Настройки сервера
	serverSettings = $state<AdminServerSettingsOut | null>(null);
	serverError = $state('');
	serverLoading = $state(false);
	conflictVersion = $state<number | null>(null);

	// Журнал действий (аудит)
	audit = $state<AdminAuditOut[]>([]);
	auditNextBefore = $state<number | null>(null);
	auditError = $state('');
	auditLoading = $state(false);

	// Уведомления сервера
	notifications = $state<AdminNotificationOut[]>([]);
	notificationsError = $state('');
	notificationsLoading = $state(false);

	constructor(api: Api) {
		this.api = api;
	}

	async loadUsers(): Promise<void> {
		this.usersLoading = true;
		this.usersError = '';
		try {
			this.users = await call(this.api.GET('/api/v1/admin/users'));
		} catch (e) {
			if (e instanceof ApiFailure && e.error.status === 404) {
				this.forbidden = true;
			}
			this.usersError = formatAdminError(e);
		} finally {
			this.usersLoading = false;
		}
	}

	async patchUser(userId: number, body: AdminUserPatchIn): Promise<AdminUserOut | null> {
		this.usersError = '';
		try {
			const updated = await call(
				this.api.PATCH('/api/v1/admin/users/{user_id}', {
					params: { path: { user_id: userId } },
					body
				})
			);
			if (this.users) {
				this.users = this.users.map((u) => (u.id === userId ? updated : u));
			}
			return updated;
		} catch (e) {
			const msg = formatAdminError(e);
			this.usersError = msg;
			throw new Error(msg);
		}
	}

	async deleteUser(userId: number, confirmLogin: string): Promise<boolean> {
		this.usersError = '';
		try {
			await call(
				this.api.DELETE('/api/v1/admin/users/{user_id}', {
					params: { path: { user_id: userId } },
					body: { confirm_login: confirmLogin }
				})
			);
			await this.loadUsers();
			return true;
		} catch (e) {
			const msg = formatAdminError(e);
			this.usersError = msg;
			throw new Error(msg);
		}
	}

	async loadAccounts(): Promise<void> {
		this.accountsLoading = true;
		this.accountsError = '';
		try {
			this.accounts = await call(this.api.GET('/api/v1/admin/accounts'));
		} catch (e) {
			if (e instanceof ApiFailure && e.error.status === 404) {
				this.forbidden = true;
			}
			this.accountsError = formatAdminError(e);
		} finally {
			this.accountsLoading = false;
		}
	}

	async patchAccount(accountId: number, blocked: boolean, reason?: string): Promise<AdminAccountOut | null> {
		this.accountsError = '';
		try {
			const updated = await call(
				this.api.PATCH('/api/v1/admin/accounts/{account_id}', {
					params: { path: { account_id: accountId } },
					body: { blocked, reason: reason ?? null }
				})
			);
			if (this.accounts) {
				this.accounts = this.accounts.map((a) => (a.id === accountId ? updated : a));
			}
			return updated;
		} catch (e) {
			const msg = formatAdminError(e);
			this.accountsError = msg;
			throw new Error(msg);
		}
	}

	async restartAccount(accountId: number): Promise<boolean> {
		this.accountsError = '';
		try {
			await call(
				this.api.POST('/api/v1/admin/accounts/{account_id}/restart', {
					params: { path: { account_id: accountId } }
				})
			);
			await this.loadAccounts();
			return true;
		} catch (e) {
			const msg = formatAdminError(e);
			this.accountsError = msg;
			throw new Error(msg);
		}
	}

	async deleteAccount(accountId: number, confirmName: string): Promise<boolean> {
		this.accountsError = '';
		try {
			await call(
				this.api.DELETE('/api/v1/admin/accounts/{account_id}', {
					params: { path: { account_id: accountId } },
					body: { confirm_name: confirmName }
				})
			);
			await this.loadAccounts();
			return true;
		} catch (e) {
			const msg = formatAdminError(e);
			this.accountsError = msg;
			throw new Error(msg);
		}
	}

	async loadInvites(): Promise<void> {
		this.invitesLoading = true;
		this.invitesError = '';
		try {
			this.invites = await call(this.api.GET('/api/v1/admin/invites'));
		} catch (e) {
			if (e instanceof ApiFailure && e.error.status === 404) {
				this.forbidden = true;
			}
			this.invitesError = formatAdminError(e);
		} finally {
			this.invitesLoading = false;
		}
	}

	async createInvite(body: AdminInviteCreateIn): Promise<AdminInviteCreatedOut | null> {
		this.invitesError = '';
		try {
			const created = await call(
				this.api.POST('/api/v1/admin/invites', {
					body
				})
			);
			if (this.invites) {
				this.invites = [created.invite, ...this.invites];
			}
			return created;
		} catch (e) {
			const msg = formatAdminError(e);
			this.invitesError = msg;
			throw new Error(msg);
		}
	}

	async revokeInvite(inviteId: number): Promise<boolean> {
		this.invitesError = '';
		try {
			await call(
				this.api.DELETE('/api/v1/admin/invites/{invite_id}', {
					params: { path: { invite_id: inviteId } }
				})
			);
			if (this.invites) {
				this.invites = this.invites.filter((i) => i.id !== inviteId);
			}
			return true;
		} catch (e) {
			const msg = formatAdminError(e);
			this.invitesError = msg;
			throw new Error(msg);
		}
	}

	async loadServerSettings(): Promise<void> {
		this.serverLoading = true;
		this.serverError = '';
		this.conflictVersion = null;
		try {
			this.serverSettings = await call(this.api.GET('/api/v1/admin/server-settings'));
		} catch (e) {
			if (e instanceof ApiFailure && e.error.status === 404) {
				this.forbidden = true;
			}
			this.serverError = formatAdminError(e);
		} finally {
			this.serverLoading = false;
		}
	}

	async patchServerSettings(version: number, changes: Record<string, unknown>): Promise<AdminServerSettingsPatchOut | null> {
		this.serverError = '';
		this.conflictVersion = null;
		try {
			const out = await call(
				this.api.PATCH('/api/v1/admin/server-settings', {
					body: { version, changes }
				})
			);
			if (this.serverSettings) {
				this.serverSettings = {
					...this.serverSettings,
					version: out.version,
					values: out.values
				};
			}
			return out;
		} catch (e) {
			if (e instanceof ApiFailure && e.error.kind === 'version_conflict') {
				this.conflictVersion = e.error.version;
				this.serverError = 'Настройки изменились, перечитать';
			} else {
				this.serverError = formatAdminError(e);
			}
			const msg = this.serverError;
			throw new Error(msg);
		}
	}

	async loadAudit(more = false): Promise<void> {
		this.auditLoading = true;
		this.auditError = '';
		try {
			const before = more ? (this.auditNextBefore ?? undefined) : undefined;
			const page = await call(
				this.api.GET('/api/v1/admin/audit', {
					params: { query: { limit: 50, ...(before ? { before } : {}) } }
				})
			);
			this.audit = more ? [...this.audit, ...page.items] : page.items;
			this.auditNextBefore = page.next_before;
		} catch (e) {
			if (e instanceof ApiFailure && e.error.status === 404) {
				this.forbidden = true;
			}
			this.auditError = formatAdminError(e);
		} finally {
			this.auditLoading = false;
		}
	}

	async loadNotifications(): Promise<void> {
		this.notificationsLoading = true;
		this.notificationsError = '';
		try {
			this.notifications = await call(
				this.api.GET('/api/v1/admin/notifications', {
					params: { query: { limit: 50 } }
				})
			);
		} catch (e) {
			if (e instanceof ApiFailure && e.error.status === 404) {
				this.forbidden = true;
			}
			this.notificationsError = formatAdminError(e);
		} finally {
			this.notificationsLoading = false;
		}
	}

	async readNotifications(upToId: number): Promise<boolean> {
		this.notificationsError = '';
		try {
			await call(
				this.api.POST('/api/v1/admin/notifications/read', {
					body: { up_to_id: upToId }
				})
			);
			if (this.notifications) {
				this.notifications = this.notifications.map((n) => (n.id <= upToId ? { ...n, read: true } : n));
			}
			return true;
		} catch (e) {
			const msg = formatAdminError(e);
			this.notificationsError = msg;
			throw new Error(msg);
		}
	}
}
