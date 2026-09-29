import { describe, expect, it } from 'vitest';
import { createApi } from '$lib/api/client';
import { json, mockFetch } from '$lib/test/fetch';
import { fixture } from '$lib/test/fixtures';
import { loadMetrics, stepSeries, type MetricsData } from './series';

const today = fixture<MetricsData & { next_cursor: null }>('metrics_today');
const win = { from: new Date('2026-09-26T21:00:00Z'), to: new Date('2026-09-27T20:27:51Z') };

describe('ряды метрик', () => {
	it('ступенчатый ряд продлевается до конца окна', () => {
		const [xs, ys] = stepSeries(today, 'money', win);
		expect(xs).toHaveLength(127);
		expect(ys[0]).toBe(676);
		expect(ys.at(-1)).toBe(47);
		expect(xs.at(-1)).toBe(win.to.getTime() / 1000);
		expect(xs[0]).toBe(new Date('2026-09-27T09:48:27Z').getTime() / 1000);
	});

	it('значение на начало окна ставится в его начало', () => {
		const data: MetricsData = {
			series: { money: [['2026-09-27T10:00:00Z', 5]] },
			initial: { money: ['2026-09-20T00:00:00Z', 3] },
			events: []
		};
		expect(stepSeries(data, 'money', win)).toEqual([
			[win.from.getTime() / 1000, new Date('2026-09-27T10:00:00Z').getTime() / 1000, win.to.getTime() / 1000],
			[3, 5, 5]
		]);
		expect(stepSeries(data, 'books', win)).toEqual([[], []]);
	});

	it('страницы подгружаются до конца окна, initial и события — с первой', async () => {
		const pages = [
			{
				series: { money: [['2026-09-27T10:00:00Z', 1]] },
				initial: { money: ['2026-09-26T00:00:00Z', 9] },
				events: [{ at: '2026-09-27T10:30:00Z', scenario: 'stocks_dump' }, { at: 5 }],
				next_cursor: 'c1'
			},
			{ series: { money: [['2026-09-27T11:00:00Z', 2]], exp: [['2026-09-27T11:00:00Z', 7]] }, initial: {}, next_cursor: null }
		];
		const fetch = mockFetch(() => json(pages.shift()));
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		const out = await loadMetrics(api, win, ['money', 'exp']);
		expect(out.series.money).toEqual([['2026-09-27T10:00:00Z', 1], ['2026-09-27T11:00:00Z', 2]]);
		expect(out.initial).toEqual({ money: ['2026-09-26T00:00:00Z', 9] });
		// Битое событие отброшено; сервер без events — пустой список.
		expect(out.events).toEqual([{ at: '2026-09-27T10:30:00Z', scenario: 'stocks_dump' }]);
		expect(fetch.calls[1]?.url).toContain('cursor=c1');
		expect(fetch.calls[0]?.url).toContain('fields=money%2Cexp');
	});
});

describe('подгрузка до конца окна', () => {
	const pageOf = (i: number, last: number) => ({
		series: { money: [[`2026-09-27T10:${String(i).padStart(2, '0')}:00Z`, i]] },
		initial: i === 0 ? { money: ['2026-09-26T00:00:00Z', -1] } : {},
		next_cursor: i < last ? `c${i + 1}` : null
	});

	it('больше 20 страниц — дочитываются все, с прогрессом', async () => {
		const fetch = mockFetch((c) => {
			const m = /cursor=c(\d+)/.exec(c.url);
			return json(pageOf(m ? Number(m[1]) : 0, 24));
		});
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		const progress: [number, number][] = [];
		const out = await loadMetrics(api, win, ['money'], { onProgress: (pages, points) => progress.push([pages, points]) });
		expect(fetch.calls).toHaveLength(25);
		expect(out.series.money).toHaveLength(25);
		expect(out.series.money?.at(-1)).toEqual(['2026-09-27T10:24:00Z', 24]);
		expect(progress.at(-1)).toEqual([25, 25]);
		expect(progress).toHaveLength(25);
	});

	it('курсор не сдвигается — ошибка, а не бесконечный цикл', async () => {
		const fetch = mockFetch(() => json({ series: {}, initial: {}, next_cursor: 'same' }));
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		await expect(loadMetrics(api, win, ['money'])).rejects.toThrow('курсор метрик не сдвигается');
		expect(fetch.calls).toHaveLength(2);
	});

	it('отмена — запрос следующей страницы не уходит', async () => {
		const ctl = new AbortController();
		const fetch = mockFetch(() => {
			ctl.abort();
			return json({ series: {}, initial: {}, next_cursor: 'c1' });
		});
		const api = createApi({ csrf: () => null, refreshCsrf: async () => null, unauthorized: () => {} }, fetch);
		await expect(loadMetrics(api, win, ['money'], { signal: ctl.signal })).rejects.toThrow();
		expect(fetch.calls).toHaveLength(1);
	});
});
