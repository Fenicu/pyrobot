import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { SettingsOut } from '$lib/api/types';
import { SettingsEditor } from '$lib/settings/editor.svelte';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import SettingsView from './SettingsView.svelte';

const settings = fixture<SettingsOut>('settings');

async function view() {
	const fetch = mockFetch((c) => {
		if (c.url.startsWith('/api/v1/settings/history')) return json(fixture('settings_history'));
		if (c.method === 'PATCH') return json({ version: 14, values: JSON.parse(c.body).changes ? settings.values : {}, changed: {}, restart_required: [] });
		return json(settings);
	});
	const api = createApi({ csrf: () => 'c', refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
	const editor = new SettingsEditor(api);
	await editor.load();
	render(SettingsView, { api, editor, now: new Date('2026-09-27T20:00:00Z') });
	return { fetch, editor };
}

describe('Настройки', () => {
	it('секция: поля по типам, только чтение, отличие от умолчания', async () => {
		const user = userEvent.setup();
		await view();
		const engine = screen.getByRole('region', { name: 'Движок' });
		expect(within(engine).getByRole('combobox', { name: 'Режим' })).toHaveValue('live');
		const killed = engine.querySelector('[data-path="engine.killed"]')!;
		expect(killed).toHaveTextContent('выкл');
		expect(killed).toHaveTextContent('только чтение');
		expect(within(engine).getAllByRole('switch').length).toBe(2);
		await user.click(screen.getByRole('button', { name: 'Стратегия и дела' }));
		const strategy = screen.getByRole('region', { name: 'Стратегия и дела' });
		const deeds = strategy.querySelector('[data-path="strategy.deeds"]')!;
		expect(deeds).toHaveTextContent('не по умолч.');
		expect(within(strategy).getByRole('group', { name: 'Основные дела' })).toHaveTextContent('harvest');
	});

	it('правка → панель «N изменений · версия V» → сохранение', async () => {
		const user = userEvent.setup();
		const { fetch } = await view();
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		await user.click(screen.getByRole('switch', { name: 'Казино' }));
		const bar = screen.getByRole('region', { name: 'Несохранённые изменения' });
		expect(bar).toHaveTextContent('1 изменение · версия 13');
		expect(screen.getByText('изменено')).toBeInTheDocument();
		await user.click(within(bar).getByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(fetch.calls.some((c) => c.method === 'PATCH')).toBe(true));
		expect(JSON.parse(fetch.calls.find((c) => c.method === 'PATCH')!.body)).toEqual({
			version: 13,
			changes: { features: { casino: true } },
			confirm_live: false
		});
		await vi.waitFor(() => expect(screen.queryByRole('region', { name: 'Несохранённые изменения' })).toBeNull());
	});

	it('«max» или число, теги, поиск', async () => {
		const user = userEvent.setup();
		const { editor } = await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'lottery.tickets');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		await user.selectOptions(within(found).getByRole('combobox', { name: '💵: вид' }), 'num');
		expect(editor.value(['lottery', 'tickets', 'money'])).toBe(0);
		await user.clear(screen.getByRole('spinbutton', { name: '💵: число' }));
		await user.type(screen.getByRole('spinbutton', { name: '💵: число' }), '4');
		expect(editor.value(['lottery', 'tickets', 'money'])).toBe(4);
		await user.clear(screen.getByRole('searchbox', { name: 'Поиск настройки' }));
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'focus');
		await user.click(screen.getByRole('button', { name: 'dconv: раньше' }));
		expect(editor.value(['strategy', 'focus'])).toEqual(['dconv', 'harvest']);
		await user.click(screen.getByRole('button', { name: 'Убрать harvest' }));
		expect(editor.value(['strategy', 'focus'])).toEqual(['dconv']);
	});

	it('история: список версий и полный diff по клику', async () => {
		const user = userEvent.setup();
		await view();
		const list = await screen.findByRole('list', { name: 'Версии настроек' });
		const v8 = await within(list).findByRole('button', { name: /v8 / });
		expect(v8).toHaveTextContent('features.daily_tasks → вкл; strategy.deeds → harvest, job, learn, dconv, walk, confa');
		await user.click(v8);
		expect(v8).toHaveAttribute('aria-expanded', 'true');
		expect(within(list).getByLabelText('Изменения версии 8')).toHaveTextContent('features.daily_tasks выкл → вкл');
	});
});
