import { MediaQuery } from 'svelte/reactivity';

/** Медиазапрос как реактивное значение: `MediaQuery` держит слушатель `change`, только пока
 * значение читают (после размонтирования слушатель снят); без matchMedia (jsdom) — false. */
export function media(query: string): { readonly current: boolean } {
	return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
		? new MediaQuery(query)
		: { current: false };
}

/** Ширина ПК-раскладки (Tailwind md). */
export const DESKTOP = '(min-width: 768px)';

/** Ширина сетки главной из 12 колонок (Tailwind xl): уже три колонки блоков не помещаются. */
export const WIDE = '(min-width: 1280px)';
