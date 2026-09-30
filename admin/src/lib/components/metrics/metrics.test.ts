import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createApi } from '$lib/api/client';
import { deferred, type Deferred } from '$lib/test/deferred';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';

// В jsdom нет canvas: ECharts подменяется, проверяются настройки, которые ему отдаёт график.
type Option = {
	series: { name: string; data: [number, number][]; markLine: { data: unknown[] } }[];
	tooltip: { formatter: (p: { axisValue: number }[]) => string };
};
const created: { options: Option[] }[] = [];
vi.mock('$lib/metrics/echarts', () => ({
	init: () => {
		const chart = {
			options: [] as Option[],
			setOption(o: Option) {
				chart.options.push(o);
			},
			on() {},
			off() {},
			dispatchAction() {},
			resize() {},
			dispose() {},
			isDisposed: () => false,
			getZr: () => ({ on() {}, off() {} }),
			getHeight: () => 180,
			convertToPixel: () => 0
		};
		created.push(chart);
		return chart;
	}
}));

const { default: MetricsView } = await import('./MetricsView.svelte');

describe('Метрики', () => {
	it('графики по полям с фикстуры за сегодня', async () => {
		const user = userEvent.setup();
		created.length = 0;
		const fetch = mockFetch(() => json(fixture('metrics_today')));
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetricsView, { api, now: new Date('2026-09-27T20:27:51Z') });
		// Метки — удачные запуски, которые двигают метрики: два слива налички в акции и сон (фикстура
		// метрик того же дня, события — по журналу 27.09).
		expect(await screen.findByRole('img', { name: 'График: 💵 деньги · 47, точек 127, меток 3' })).toBeInTheDocument();
		expect(screen.getByText('Метки на графиках: 📈 слив налички в акции · 🛌 сон')).toBeInTheDocument();
		expect(fetch.calls[0]?.url).toContain('from=2026-09-26T21%3A00%3A00.000Z');
		const first = () => created.map((c) => c.options[0]!.series[0]!);
		const money = first().find((sr) => sr.name === '💵 деньги')!;
		expect(money.data.at(-1)?.[1]).toBe(47);
		// Три метки окна — пунктирные вертикали на каждом графике.
		expect(money.markLine.data).toHaveLength(3);
		expect(first().map((sr) => sr.name)).toEqual(['💵 деньги', '💡 опыт', '🔥 мотивация', '🔋 выносливость']);
		// Подсказка — точное значение в момент курсора (время оси — МСК, сдвинутое на +3 ч).
		const tip = created[0]!.options[0]!.tooltip.formatter([{ axisValue: Date.UTC(2026, 8, 27, 13, 6) + 3 * 3_600_000 }]);
		expect(tip).toContain('27.09 16:06');
		expect(tip).toContain('💵 деньги: <b>3\u00a0252</b>');
		expect(tip).not.toContain('📈');
		// Уровня среди полей нет: его график почти всегда прямая.
		expect(screen.queryByRole('button', { name: 'уровень' })).toBeNull();
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

describe('Метрики: «сегодня» по общему тикеру', () => {
	afterEach(() => vi.useRealTimers());

	it('после полуночи по МСК окно «сегодня» — новые сутки, в течение суток без перечитывания', async () => {
		vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
		vi.setSystemTime(new Date('2026-09-27T20:59:00Z'));
		const fetch = mockFetch(() => json({ series: {}, initial: {}, next_cursor: null }));
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetricsView, { api });
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		expect(fetch.calls[0]?.url).toContain('from=2026-09-26T21%3A00%3A00.000Z');
		expect(fetch.calls[0]?.url).toContain('to=2026-09-27T20%3A59%3A00.000Z');
		vi.advanceTimersByTime(60_000);
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(2));
		expect(fetch.calls[1]?.url).toContain('from=2026-09-27T21%3A00%3A00.000Z');
		vi.advanceTimersByTime(5 * 60_000);
		await new Promise((r) => setTimeout(r, 10));
		expect(fetch.calls).toHaveLength(2);
	});

	it('окно берёт текущий момент при выборе, а не при открытии страницы', async () => {
		const user = userEvent.setup();
		vi.useFakeTimers({ toFake: ['Date'] });
		vi.setSystemTime(new Date('2026-09-27T10:00:00Z'));
		const fetch = mockFetch(() => json({ series: {}, initial: {}, next_cursor: null }));
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		render(MetricsView, { api });
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(1));
		vi.setSystemTime(new Date('2026-09-27T15:00:00Z'));
		await user.click(screen.getByRole('button', { name: '7 дней' }));
		await vi.waitFor(() => expect(fetch.calls).toHaveLength(2));
		expect(fetch.calls[1]?.url).toContain('to=2026-09-27T15%3A00%3A00.000Z');
	});
});
