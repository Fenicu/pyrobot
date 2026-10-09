import { cleanup, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { AccountOut } from '$lib/api/types';
import AccountSwitcher from './AccountSwitcher.svelte';

const account = (id: number, name: string, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name,
	status: 'enabled',
	status_reason: null,
	blocked: false,
	blocked_reason: null,
	tg: { user_id: 100 + id, online: true },
	mode: 'live',
	paused: false,
	killed: false,
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

const accounts = [
	account(1, 'main', { unread: { warn: 1, error: 1 } }),
	account(2, 'twink', { tg: { user_id: 102, online: false }, unread: { warn: 2, error: 1 } }),
	account(3, 'old', { status: 'error', status_reason: 'session_revoked' })
];

describe('переключатель аккаунтов', () => {
	it('список «Ещё»: имя, статус и счётчик; ведёт в тот же раздел другого аккаунта', () => {
		// Счётчик открытого аккаунта — из его потока, у остальных — из списка.
		render(AccountSwitcher, { variant: 'list', accounts, current: 1, alerts: 4, path: '/a/1/journal' });
		const list = screen.getByRole('list', { name: 'Аккаунты' });
		const links = within(list).getAllByRole('link');
		expect(links.map((a) => [a.textContent?.replace(/\s+/g, ' ').trim(), a.getAttribute('href')])).toEqual([
			['main (в сети) 4', '/a/1/journal'],
			['twink (Telegram не в сети) 3', '/a/2/journal'],
			['old (ошибка: session_revoked)', '/a/3/journal'],
			['Все аккаунты', '/accounts']
		]);
		expect(links[0]).toHaveAttribute('aria-current', 'true');
	});

	it('имя — титул как в игре: значок компании и [TAG]; сырое имя не меняется', () => {
		const titled = [
			account(1, 'Fenicu', { company: 'bmesa', team_tag: 'SU' }),
			account(2, 'twink', { company: 'wayne' }),
			account(3, 'old')
		];
		render(AccountSwitcher, { variant: 'list', accounts: titled, current: 1, path: '/a/1' });
		const links = within(screen.getByRole('list', { name: 'Аккаунты' })).getAllByRole('link');
		expect(links.map((a) => a.textContent?.replace(/\s+/g, ' ').trim())).toEqual([
			'☣️[SU] Fenicu (в сети)',
			'🎩twink (в сети)',
			'old (в сети)',
			'Все аккаунты'
		]);
	});

	it('на телефоне: плашка и список «Ещё» показывают титул', () => {
		const titled = [account(1, 'Fenicu', { company: 'bmesa', team_tag: 'SU' })];
		render(AccountSwitcher, { variant: 'bar', accounts: titled, current: 1, path: '/a/1' });
		expect(screen.getByRole('button', { name: /☣️\[SU\] Fenicu/ })).toBeInTheDocument();
		cleanup();
		render(AccountSwitcher, { variant: 'list', accounts: titled, current: 1, path: '/a/1' });
		expect(screen.getByRole('link', { name: /☣️\[SU\] Fenicu/ })).toBeInTheDocument();
	});
});
