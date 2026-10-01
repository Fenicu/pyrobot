import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { AccountOut } from '$lib/api/types';
import AccountSwitcher from './AccountSwitcher.svelte';

const account = (id: number, name: string, over: Partial<AccountOut> = {}): AccountOut => ({
	id,
	name,
	status: 'enabled',
	status_reason: null,
	tg: { user_id: 100 + id, online: true },
	mode: 'live',
	paused: false,
	killed: false,
	last_action_at: null,
	unread: { warn: 0, error: 0 },
	...over
});

const accounts = [
	account(1, 'main', { unread: { warn: 1, error: 1 } }),
	account(2, 'twink', { tg: { user_id: 102, online: false }, unread: { warn: 2, error: 1 } }),
	account(3, 'old', { status: 'error', status_reason: 'session_revoked' })
];

describe('переключатель аккаунтов', () => {
	it('на ПК: имя, статус и счётчик открытого; список ведёт в тот же раздел другого аккаунта', async () => {
		const user = userEvent.setup();
		// Счётчик открытого аккаунта — из его потока, у остальных — из списка.
		render(AccountSwitcher, { variant: 'side', accounts, current: 1, alerts: 4, path: '/a/1/journal' });
		const toggle = screen.getByRole('button', { expanded: false });
		expect(toggle).toHaveTextContent('main');
		expect(toggle).toHaveTextContent('(в сети)');
		expect(within(toggle).getByLabelText('непрочитанных предупреждений и ошибок: 4')).toBeInTheDocument();

		await user.click(toggle);
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
});
