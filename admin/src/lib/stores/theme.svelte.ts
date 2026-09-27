export type ThemePref = 'dark' | 'light' | 'system';
const KEY = 'pyrobot.theme';

/** Тема: тёмная по умолчанию; выбор хранится в localStorage (не секрет). */
export class Theme {
	pref = $state<ThemePref>('dark');
	#media: MediaQueryList | null = null;

	init(): void {
		const saved = localStorage.getItem(KEY);
		if (saved === 'light' || saved === 'system' || saved === 'dark') this.pref = saved;
		this.#media = window.matchMedia?.('(prefers-color-scheme: light)') ?? null;
		this.#media?.addEventListener('change', () => this.apply());
		this.apply();
	}

	set(pref: ThemePref): void {
		this.pref = pref;
		localStorage.setItem(KEY, pref);
		this.apply();
	}

	apply(): void {
		const light = this.pref === 'light' || (this.pref === 'system' && !!this.#media?.matches);
		document.documentElement.classList.toggle('light', light);
		document.documentElement.classList.toggle('dark', !light);
	}
}

export const theme = new Theme();
