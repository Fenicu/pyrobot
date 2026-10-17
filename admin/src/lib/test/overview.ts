import type { AccountOut } from '$lib/api/types';

/** Аккаунт со всеми полями `GET /accounts`; `over` переопределяет нужные. */
export const accountOut = (id: number, name: string, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: 100 + id, online: true, unreachable_since: null },
	mode: 'live',
	paused: false,
	killed: false,
	manual_control_until: null,
	last_action_at: null,
	unread: { warn: 0, error: 0 },
	company: null,
	team_tag: null,
	level: null,
	busy: null,
	in_metro: false,
	alert: null,
	...over
});
