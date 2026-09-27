/** Медиазапрос как реактивное значение; без matchMedia (тесты) — false. */
export class Media {
	matches = $state(false);

	constructor(query: string) {
		if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
		const m = window.matchMedia(query);
		this.matches = m.matches;
		m.addEventListener('change', (e) => (this.matches = e.matches));
	}
}

/** Ширина ПК-раскладки (Tailwind md). */
export const DESKTOP = '(min-width: 768px)';
