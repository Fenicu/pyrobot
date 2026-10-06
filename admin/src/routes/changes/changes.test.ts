import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import { parseChangelog } from '$lib/changelog';
import { APP_VERSION } from '$lib/stores/whatsnew.svelte';
import ChangesPage from './+page.svelte';

describe('страница истории изменений', () => {
	it('установленная версия и все записи CHANGES.rst, свежая первой', () => {
		render(ChangesPage);
		expect(screen.getByRole('heading', { level: 1, name: 'История изменений' })).toBeInTheDocument();
		expect(screen.getByText(APP_VERSION)).toBeInTheDocument();
		const versions = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
		expect(versions).toEqual(parseChangelog().map((e) => e.version));
	});
});
