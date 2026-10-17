import { cleanup, render } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import Root from './+page.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => {}) }));
vi.mock('$lib/app.svelte', () => ({ accounts: { list: null, error: null, load: async () => {} } }));

afterEach(() => {
	cleanup();
	vi.clearAllMocks();
});

describe('корень', () => {
	it('«/» ведёт в «Обзор» сразу, не дожидаясь списка аккаунтов', () => {
		render(Root);
		expect(goto).toHaveBeenCalledWith('/overview', { replaceState: true });
	});
});
