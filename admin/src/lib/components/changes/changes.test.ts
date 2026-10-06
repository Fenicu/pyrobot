import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ChangelogEntry } from '$lib/changelog';
import WhatsNewDialog from './WhatsNewDialog.svelte';

const entries: ChangelogEntry[] = [
	{
		version: '0.18.0',
		date: '07.10.2026',
		sections: [{ title: 'Добавлено', items: ['Покупка гаджетов в фоне', '<b>текст</b>'] }],
		preamble: []
	},
	{
		version: '0.17.3',
		date: '06.10.2026',
		sections: [{ title: 'Исправлено', items: ['Вход в Telegram'] }],
		preamble: []
	}
];

describe('окно «Что нового»', () => {
	it('версии, даты, разделы и пункты; пункты — только текстом', async () => {
		render(WhatsNewDialog, { entries, onclose: vi.fn() });
		const dialog = await screen.findByRole('dialog', { name: 'Что нового' });
		expect(dialog).toHaveTextContent('0.18.0');
		expect(dialog).toHaveTextContent('07.10.2026');
		expect(dialog).toHaveTextContent('Добавлено');
		expect(dialog).toHaveTextContent('Покупка гаджетов в фоне');
		expect(dialog).toHaveTextContent('0.17.3');
		expect(dialog.querySelector('b')).toBeNull();
	});

	it('ссылка на всю историю ведёт на /changes и закрывает окно', async () => {
		const user = userEvent.setup();
		const onclose = vi.fn();
		render(WhatsNewDialog, { entries, onclose });
		const link = await screen.findByRole('link', { name: 'Вся история изменений' });
		expect(link).toHaveAttribute('href', '/changes');
		link.addEventListener('click', (e) => e.preventDefault());
		await user.click(link);
		expect(onclose).toHaveBeenCalled();
	});

	it('«Понятно» и Esc закрывают окно', async () => {
		const user = userEvent.setup();
		const onclose = vi.fn();
		render(WhatsNewDialog, { entries, onclose });
		await user.click(await screen.findByRole('button', { name: 'Понятно' }));
		await user.keyboard('{Escape}');
		expect(onclose).toHaveBeenCalledTimes(2);
	});
});
