import { render, screen } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { page } from '$lib/test/page.svelte';
import LegacyRoute from './legacy-route.test.svelte';

vi.mock('$app/state', async () => ({
	page: (await import('$lib/test/page.svelte')).page,
	updated: (await import('$lib/test/updated.svelte')).updated
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}), onNavigate: () => {} }));
vi.mock('$lib/app.svelte', () => ({
	session: { status: 'authenticated', offline: false, start: async () => {}, stop: () => {} },
	accounts: { list: [{ id: 2 }, { id: 5 }], error: null, load: async () => {} },
	startApp: () => {},
	stopApp: () => {}
}));

afterEach(() => localStorage.clear());

describe('старая ссылка без аккаунта', () => {
	it('своя страница ожидания без оболочки, затем тот же экран последнего аккаунта с хвостом и query', async () => {
		localStorage.setItem('pyrobot.account', '5');
		page.url = new URL('http://app.invalid/journal?type=action#x');
		render(LegacyRoute);
		expect(screen.getByText('Загрузка…')).toBeInTheDocument();
		expect(screen.queryByRole('navigation')).toBeNull();
		await vi.waitFor(() => expect(goto).toHaveBeenCalledWith('/a/5/journal?type=action#x', { replaceState: true }));
	});
});
