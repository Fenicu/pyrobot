import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { SettingsOut } from '$lib/api/types';
import { SettingsEditor } from '$lib/settings/editor.svelte';
import { toasts } from '$lib/stores/toasts.svelte';
import { json, mockFetch, type Call } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import SettingsView from './SettingsView.svelte';

const settings = fixture<SettingsOut>('settings');

async function view(patch?: (c: Call) => Response, current: () => SettingsOut = () => settings) {
	const fetch = mockFetch((c) => {
		if (c.url.startsWith('/api/v1/settings/history')) return json(fixture('settings_history'));
		if (c.method === 'PATCH' && patch) return patch(c);
		if (c.method === 'PATCH') return json({ version: 14, values: JSON.parse(c.body).changes ? settings.values : {}, changed: {}, restart_required: [] });
		return json(current());
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

	it('описание под полем, секции — своё; ⓘ раскрывает описание на телефоне', async () => {
		const user = userEvent.setup();
		await view();
		const engine = screen.getByRole('region', { name: 'Движок' });
		expect(engine).toHaveTextContent('Режим, пауза, kill switch и темп шлюза');
		const row = engine.querySelector('[data-path="engine.min_request_interval_s"]')!;
		const help = within(row as HTMLElement).getByText(/Минимальный интервал между любыми двумя отправками/);
		// На ПК описание видно всегда (md:block), на телефоне скрыто до нажатия ⓘ.
		expect(help).toHaveClass('hidden', 'md:block');
		const info = within(row as HTMLElement).getByRole('button', { name: 'Описание: Пауза между запросами, с' });
		expect(info).toHaveAttribute('aria-expanded', 'false');
		expect(info).toHaveAttribute('aria-controls', help.id);
		expect(info).toHaveClass('md:hidden');
		info.focus();
		await user.keyboard('{Enter}');
		expect(info).toHaveAttribute('aria-expanded', 'true');
		expect(help).not.toHaveClass('hidden');
		await user.click(info);
		expect(help).toHaveClass('hidden');
	});

	it('поиск находит настройку по описанию', async () => {
		const user = userEvent.setup();
		await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'антифлуд');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		const paths = [...found.querySelectorAll('[data-path]')].map((e) => e.getAttribute('data-path'));
		// «Пауза между запросами» — только по описанию: в пути и подписи слова нет.
		expect(paths).toContain('engine.min_request_interval_s');
		expect(paths).toContain('engine.antiflood_retry_max');
	});

	it('«max» или число, теги, поиск', async () => {
		const user = userEvent.setup();
		const { editor } = await view();
		await user.type(screen.getByRole('searchbox', { name: 'Поиск настройки' }), 'lottery.tickets');
		const found = screen.getByRole('region', { name: 'Найденные настройки' });
		await user.selectOptions(within(found).getByRole('combobox', { name: 'Билеты за 💵: вид' }), 'num');
		expect(editor.value(['lottery', 'tickets', 'money'])).toBe(0);
		await user.clear(screen.getByRole('spinbutton', { name: 'Билеты за 💵: число' }));
		await user.type(screen.getByRole('spinbutton', { name: 'Билеты за 💵: число' }), '4');
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

	it('чужая версия из SSE перечитывает и историю', async () => {
		let version = 13;
		const { fetch, editor } = await view(undefined, () => ({ ...settings, version }));
		const history = () => fetch.calls.filter((c) => c.url.startsWith('/api/v1/settings/history')).length;
		await vi.waitFor(() => expect(history()).toBe(1));
		version = 14;
		editor.onEvent({ type: 'settings', id: 'e:1', data: { version: 14, mode: 'live', paused: false, killed: false } });
		await vi.waitFor(() => expect(editor.version).toBe(14));
		await vi.waitFor(() => expect(history()).toBe(2));
	});

	it('422 без подходящего поля — сообщение с текстом ошибки, а не «поля подсвечены»', async () => {
		const user = userEvent.setup();
		toasts.items = [];
		await view(() =>
			json({ detail: [{ loc: ['body', 'changes', 'features'], msg: 'Value error, bad combo', type: 'value_error' }] }, 422)
		);
		await user.click(screen.getByRole('button', { name: 'Функции' }));
		await user.click(screen.getByRole('switch', { name: 'Казино' }));
		await user.click(screen.getByRole('button', { name: 'Сохранить' }));
		await vi.waitFor(() => expect(toasts.items.map((t) => t.text)).toEqual(['features: Value error, bad combo']));
	});
});
