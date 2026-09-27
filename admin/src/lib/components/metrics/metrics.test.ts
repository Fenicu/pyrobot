import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';

// В jsdom нет canvas: uPlot подменяется, проверяются данные, которые ему отдаёт график.
const created: { data: [number[], number[]]; opts: { series: { label?: string }[] } }[] = [];
vi.mock('uplot', () => {
	class FakePlot {
		static tzDate = (d: Date) => d;
		static paths = { stepped: () => () => null };
		constructor(opts: never, data: never) {
			created.push({ opts, data });
		}
		setSize() {}
		destroy() {}
	}
	return { default: FakePlot };
});

const { default: MetricsView } = await import('./MetricsView.svelte');

describe('Метрики', () => {
	it('графики по полям с фикстуры за сегодня', async () => {
		const user = userEvent.setup();
		created.length = 0;
		const fetch = mockFetch(() => json(fixture('metrics_today')));
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetricsView, { api, now: new Date('2026-09-27T20:27:51Z') });
		expect(await screen.findByRole('img', { name: 'График: 💵 деньги · 47, точек 127' })).toBeInTheDocument();
		expect(fetch.calls[0]?.url).toContain('from=2026-09-26T21%3A00%3A00.000Z');
		const money = created.find((c) => c.opts.series[1]?.label === '💵 деньги · 47');
		expect(money?.data[1].at(-1)).toBe(47);
		expect(created.map((c) => c.opts.series[1]?.label?.split(' · ')[0])).toEqual([
			'💵 деньги',
			'💡 опыт',
			'🔥 мотивация',
			'🔋 выносливость'
		]);
		await user.click(screen.getByRole('button', { name: '⚙️ детали' }));
		expect(await screen.findByRole('img', { name: /⚙️ детали/ })).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: '7 дней' }));
		await vi.waitFor(() => expect(fetch.calls.at(-1)?.url).toContain('from=2026-09-20T20%3A27%3A51.000Z'));
	});
});

describe('Метрики: гонки и прогресс', () => {
	it('поздний ответ прежнего периода не заменяет график', async () => {
		const user = userEvent.setup();
		created.length = 0;
		const answers: Deferred<unknown>[] = [];
		const fetch = mockFetch(async () => {
			const d = deferred<unknown>();
			answers.push(d);
			return json(await d.promise);
		});
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetricsView, { api, now: new Date('2026-09-27T20:27:51Z') });
		await vi.waitFor(() => expect(answers).toHaveLength(1));
		await user.click(screen.getByRole('button', { name: '7 дней' }));
		await vi.waitFor(() => expect(answers).toHaveLength(2));
		const week = { series: { money: [['2026-09-22T10:00:00Z', 7]] }, initial: {}, next_cursor: null };
		answers[1]!.resolve(week);
		expect(await screen.findByRole('img', { name: 'График: 💵 деньги · 7, точек 2' })).toBeInTheDocument();
		answers[0]!.resolve(fixture('metrics_today'));
		await new Promise((r) => setTimeout(r, 20));
		expect(screen.getByRole('img', { name: 'График: 💵 деньги · 7, точек 2' })).toBeInTheDocument();
		expect(screen.queryByRole('img', { name: /💵 деньги · 47/ })).toBeNull();
	});

	it('много страниц — прогресс загрузки', async () => {
		created.length = 0;
		const answers: Deferred<unknown>[] = [];
		const fetch = mockFetch(async () => {
			const d = deferred<unknown>();
			answers.push(d);
			return json(await d.promise);
		});
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetricsView, { api, now: new Date('2026-09-27T20:27:51Z') });
		await vi.waitFor(() => expect(answers).toHaveLength(1));
		answers[0]!.resolve({ series: { money: [['2026-09-27T10:00:00Z', 1], ['2026-09-27T11:00:00Z', 2]] }, initial: {}, next_cursor: 'c1' });
		await vi.waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Загрузка… страниц: 1, точек: 2'));
		await vi.waitFor(() => expect(answers).toHaveLength(2));
		answers[1]!.resolve({ series: { money: [['2026-09-27T12:00:00Z', 3]] }, initial: {}, next_cursor: null });
		expect(await screen.findByRole('img', { name: 'График: 💵 деньги · 3, точек 4' })).toBeInTheDocument();
		expect(screen.queryByRole('status')).toBeNull();
	});
});
