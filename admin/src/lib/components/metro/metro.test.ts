import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import type { MetroRunDetail, MetroRunSummary } from '$lib/api/types';
import { deferred, flush } from '$lib/test/deferred';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import MetroRun from './MetroRun.svelte';
import MetroView from './MetroView.svelte';

const run = fixture<MetroRunDetail>('metro_run_1');
const NOW = new Date('2026-09-27T20:00:00Z');

afterEach(() => {
	vi.useRealTimers();
});

describe('карта метро', () => {
	it('SVG-сетка, маршрут, события, S/E и проигрывание', async () => {
		render(MetroRun, { run, now: NOW });
		const map = screen.getByRole('img', { name: /Карта забега #1: 197 шагов/ });
		expect(map.querySelectorAll('rect[data-cell]')).toHaveLength(285);
		expect(map.querySelector('rect[data-cell="14,1"]')?.getAttribute('data-sym')).toBe('E');
		expect(map.querySelectorAll('circle[data-event]').length).toBeGreaterThan(20);
		const texts = [...map.querySelectorAll('text')].map((t) => t.textContent);
		expect(texts).toEqual(['S', 'E']);
		// Сначала — весь забег.
		expect(screen.getByText(/шаг 197\/197/)).toHaveTextContent('клетка (14,1) · 🔋 100 · аптечки 6');
		const route = () => map.querySelector('polyline[data-role="route"]')!.getAttribute('points')!.split(' ');
		expect(route()).toHaveLength(198);
		await fireEvent.input(screen.getByRole('slider', { name: 'Шаг' }), { target: { value: '120' } });
		expect(screen.getByText(/шаг 120\/197/)).toHaveTextContent('клетка (10,-2) · 🔋 98 · аптечки 7');
		expect(route()).toHaveLength(121);
		await fireEvent.click(screen.getByRole('button', { name: 'В начало' }));
		expect(screen.getByText(/шаг 0\/197/)).toHaveTextContent('клетка (0,0) · 🔋 121 · аптечки 7');
		await fireEvent.click(screen.getByRole('button', { name: 'Шаг вперёд' }));
		expect(screen.getByText(/шаг 1\/197/)).toBeInTheDocument();
	});

	it('значок клетки — последнее событие к текущему шагу', async () => {
		render(MetroRun, { run, now: NOW });
		const map = screen.getByRole('img', { name: /Карта забега #1/ });
		const mark = () => map.querySelector('circle[data-pos="14,1"]')!;
		const slider = screen.getByRole('slider', { name: 'Шаг' });
		expect(mark().getAttribute('data-event')).toBe('metro_finished');
		await fireEvent.input(slider, { target: { value: '180' } });
		expect(mark().getAttribute('data-event')).toBe('metro_exit');
		expect(mark().getAttribute('opacity')).toBe('1');
		// Событий клетки ещё не было — первое будущее, приглушённо.
		await fireEvent.input(slider, { target: { value: '100' } });
		expect(mark().getAttribute('data-event')).toBe('metro_exit');
		expect(mark().getAttribute('opacity')).toBe('0.35');
		expect(map.querySelector('circle[data-pos="6,0"]')!.getAttribute('data-event')).toBe('metro_chest_opened');
		await fireEvent.input(slider, { target: { value: '37' } });
		expect(map.querySelector('circle[data-pos="6,0"]')!.getAttribute('data-event')).toBe('metro_chest');
	});

	it('проигрывание: по умолчанию ×3 (25 шагов/с), ×8 — весь забег за ~3 с, выбор запоминается', async () => {
		localStorage.removeItem('pyrobot.metro.speed');
		vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
		render(MetroRun, { run, now: NOW });
		const speed = screen.getByRole('group', { name: 'Скорость' });
		expect(within(speed).getByRole('button', { name: '×3' })).toHaveAttribute('aria-pressed', 'true');
		await fireEvent.click(screen.getByRole('button', { name: 'В начало' }));
		await fireEvent.click(screen.getByRole('button', { name: 'Проиграть' }));
		vi.advanceTimersByTime(1000);
		await flush();
		expect(screen.getByText(/шаг 25\/197/)).toBeInTheDocument();
		await fireEvent.click(within(speed).getByRole('button', { name: '×8' }));
		expect(localStorage.getItem('pyrobot.metro.speed')).toBe('60');
		vi.advanceTimersByTime(3000);
		await flush();
		expect(screen.getByText(/шаг 197\/197/)).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'Проиграть' })).toBeInTheDocument();
	});

	it('итог: бафы, награды, события', () => {
		render(MetroRun, { run, now: NOW });
		const region = screen.getByRole('region', { name: 'Забег #1' });
		expect(region).toHaveTextContent('вышел сам');
		expect(region).toHaveTextContent('бафы: fastMove, strong, firstAid');
		expect(region).toHaveTextContent('💵 349');
		expect(region).toHaveTextContent('лут ×14');
		expect(region).toHaveTextContent('197 ш · 9 мин · 2.4 с/шаг · 2.1 ш/клетку');
	});

	it('пустой забег — без карты', () => {
		render(MetroRun, {
			run: { ...run, id: 2, grid: {}, path: [], events: [], vitals: [], result: null, status: 'failed', summary: {} },
			now: NOW
		});
		expect(screen.getByText(/Карты нет/)).toBeInTheDocument();
		expect(screen.getByRole('region', { name: 'Забег #2' })).toHaveTextContent('остановлен');
	});

	it('список забегов и сводка', async () => {
		const fetch = mockFetch((c) =>
			c.url.startsWith('/api/v1/metro/runs?') ? json(fixture('metro_runs')) : json(run)
		);
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetroView, { api, now: NOW });
		const list = await screen.findByRole('list', { name: 'Забеги метро' });
		expect(await within(list).findByRole('button', { name: /13:11 · 197 ш · 9 мин/ })).toHaveAttribute('aria-pressed', 'true');
		expect(await screen.findByRole('img', { name: /Карта забега #1/ })).toBeInTheDocument();
		expect(screen.getByLabelText('Сводка забегов')).toHaveTextContent('p90 длительности: 9 мин');
		expect(fetch.calls.map((c) => c.url)).toContain('/api/v1/metro/runs/1');
	});

	it('сводка: шагов на клетку и исходы долями', async () => {
		const base = fixture<{ items: MetroRunSummary[] }>('metro_runs').items[0]!;
		const items = [
			{ ...base, id: 3, steps: 120, visited: 40 },
			{ ...base, id: 2, steps: 60, visited: 40, summary: { ...base.summary, mode: 'explore' } },
			{ ...base, id: 1, steps: 20, visited: 0, status: 'failed' },
			{ ...base, id: 0, steps: 10, visited: 20 }
		];
		const fetch = mockFetch((c) =>
			c.url.startsWith('/api/v1/metro/runs?') ? json({ items, next_before: null }) : json(run)
		);
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetroView, { api, now: NOW });
		const stats = await screen.findByLabelText('Сводка забегов');
		expect(stats).toHaveTextContent('шагов на клетку: 1.9');
		expect(stats).toHaveTextContent('вышел сам 50% · выброс 25% · остановлен 25%');
	});

	it('ошибка прежнего забега не показывается у нового', async () => {
		const base = fixture<{ items: MetroRunSummary[] }>('metro_runs').items[0]!;
		const late = deferred<Response>();
		const fetch = mockFetch((c) => {
			if (c.url.startsWith('/api/v1/metro/runs?')) return json({ items: [{ ...base, id: 2 }, base], next_before: null });
			return c.url === '/api/v1/metro/runs/2' ? late.promise : json(run);
		});
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetroView, { api, now: NOW });
		const list = await screen.findByRole('list', { name: 'Забеги метро' });
		const buttons = await within(list).findAllByRole('button');
		await fireEvent.click(buttons[1]!);
		expect(await screen.findByRole('img', { name: /Карта забега #1/ })).toBeInTheDocument();
		late.resolve(json({ detail: 'metro run not found' }, 404));
		await flush();
		expect(screen.queryByRole('alert')).toBeNull();
		expect(screen.getByRole('img', { name: /Карта забега #1/ })).toBeInTheDocument();
	});
});
