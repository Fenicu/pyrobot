import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Clock } from './clock.svelte';
import { mskDay } from './format';

describe('общий тикер «сейчас»', () => {
	afterEach(() => vi.useRealTimers());

	it('читатель в эффекте пересчитывается раз в минуту, таймер — только пока есть читатели', () => {
		vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
		vi.setSystemTime(new Date('2026-09-27T20:59:30Z'));
		const clock = new Clock();
		const days: string[] = [];
		const stop = $effect.root(() => {
			const day = $derived(mskDay(clock.now));
			$effect(() => {
				days.push(day);
			});
		});
		flushSync();
		expect(days).toEqual(['2026-09-27']);
		expect(vi.getTimerCount()).toBe(1);
		vi.advanceTimersByTime(60_000);
		flushSync();
		expect(days).toEqual(['2026-09-27', '2026-09-28']);
		// Та же дата — эффект не перезапускается.
		vi.advanceTimersByTime(60_000);
		flushSync();
		expect(days).toHaveLength(2);
		stop();
		return vi.waitFor(() => expect(vi.getTimerCount()).toBe(0));
	});

	it('вне эффектов — просто текущий момент', () => {
		vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
		vi.setSystemTime(new Date('2026-09-27T10:00:00Z'));
		const clock = new Clock();
		expect(clock.now.toISOString()).toBe('2026-09-27T10:00:00.000Z');
		expect(vi.getTimerCount()).toBe(0);
	});
});
