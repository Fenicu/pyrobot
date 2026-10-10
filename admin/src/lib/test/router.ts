import { onMount } from 'svelte';
import type { BeforeNavigate, NavigationType, OnNavigate } from '@sveltejs/kit';

type Before = (nav: BeforeNavigate) => void;
type On = (nav: OnNavigate) => void;

/** Подделка маршрутизатора SvelteKit для `$app/navigation`, как в client.js 2.70: обработчики
 * регистрируются в onMount; `beforeNavigate` — все по очереди, `cancel()` любого останавливает
 * переход до `onNavigate`. */
export function fakeRouter(start = 'https://sw.example/') {
	const before = new Set<Before>();
	const on = new Set<On>();
	const register = <T>(set: Set<T>, fn: T) =>
		onMount(() => {
			set.add(fn);
			return () => set.delete(fn);
		});
	const router = {
		url: new URL(start),
		beforeNavigate: (fn: Before) => register(before, fn),
		onNavigate: (fn: On) => register(on, fn),
		/** Переход внутри приложения; false — отменён в `beforeNavigate`. */
		navigate(href: string | URL, type: Exclude<NavigationType, 'enter' | 'leave'> = 'link'): boolean {
			const from = { url: router.url, params: {}, route: { id: null } };
			const to = { url: new URL(href, router.url), params: {}, route: { id: null } };
			let cancelled = false;
			const nav = { from, to, type, willUnload: false, complete: Promise.resolve() };
			before.forEach((fn) =>
				fn({
					...nav,
					cancel: () => {
						cancelled = true;
					}
				} as BeforeNavigate)
			);
			if (cancelled) return false;
			on.forEach((fn) => fn(nav as OnNavigate));
			router.url = to.url;
			return true;
		},
		goto: async (href: string | URL) => void router.navigate(href, 'goto')
	};
	return router;
}
