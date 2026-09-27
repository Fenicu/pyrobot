import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import { createApi } from '$lib/api/client';
import type { MetroRunDetail } from '$lib/api/types';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import MetroRun from './MetroRun.svelte';
import MetroView from './MetroView.svelte';

const run = fixture<MetroRunDetail>('metro_run_1');
const NOW = new Date('2026-09-27T20:00:00Z');

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
});
