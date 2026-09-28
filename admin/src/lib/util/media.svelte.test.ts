import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { media } from './media.svelte';

describe('медиазапрос', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('слушатель change снимается, когда значение больше не читают', async () => {
		const listeners = new Set<EventListenerOrEventListenerObject>();
		let matches = false;
		vi.stubGlobal('matchMedia', (query: string) => ({
			media: query,
			get matches() {
				return matches;
			},
			addEventListener: (_: string, fn: EventListenerOrEventListenerObject) => listeners.add(fn),
			removeEventListener: (_: string, fn: EventListenerOrEventListenerObject) => listeners.delete(fn)
		}));
		const desktop = media('(min-width: 768px)');
		const seen: boolean[] = [];
		const stop = $effect.root(() => {
			$effect(() => {
				seen.push(desktop.current);
			});
		});
		flushSync();
		expect(listeners.size).toBe(1);
		matches = true;
		for (const l of listeners) (l as EventListener)(new Event('change'));
		flushSync();
		expect(seen).toEqual([false, true]);
		stop();
		await vi.waitFor(() => expect(listeners.size).toBe(0));
	});

	it('без matchMedia (jsdom) — false', () => {
		expect(media('(min-width: 768px)').current).toBe(false);
	});
});
