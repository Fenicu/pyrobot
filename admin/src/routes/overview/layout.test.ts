import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { page } from '$lib/test/page.svelte';
import Route from './layout-route.test.svelte';

vi.mock('$app/state', async () => ({ page: (await import('$lib/test/page.svelte')).page }));

function open(pathname: string) {
	page.params = {};
	page.url = new URL(pathname, 'http://app.invalid');
	return render(Route);
}

const subnav = () => screen.getByRole('navigation', { name: 'Подразделы обзора' });

afterEach(cleanup);

describe('макет «Обзора»', () => {
	it('пять подразделов - реальные ссылки, ссылка на аккаунты рядом', () => {
		open('/overview');
		expect(screen.getByRole('heading', { level: 1, name: 'Обзор' })).toBeInTheDocument();
		const links = within(subnav()).getAllByRole('link');
		expect(links.map((a) => [a.textContent?.trim(), a.getAttribute('href')])).toEqual([
			['Сводка', '/overview'],
			['Итоги', '/overview/totals'],
			['Лента', '/overview/feed'],
			['Расписание', '/overview/schedule'],
			['Штаб битвы', '/overview/battle']
		]);
		expect(screen.getByRole('link', { name: 'Аккаунты' })).toHaveAttribute('href', '/accounts');
	});

	it('активен ровно один подраздел: «Сводка» только на самой /overview', () => {
		const active = () =>
			within(subnav())
				.getAllByRole('link')
				.filter((a) => a.getAttribute('aria-current') === 'page')
				.map((a) => a.textContent?.trim());
		open('/overview');
		expect(active()).toEqual(['Сводка']);
		cleanup();
		open('/overview/battle');
		expect(active()).toEqual(['Штаб битвы']);
		cleanup();
		open('/overview/totals');
		expect(active()).toEqual(['Итоги']);
	});

	it('содержимое подраздела рисуется под полосой', () => {
		open('/overview');
		expect(screen.getByText('Содержимое подраздела')).toBeInTheDocument();
	});
});
