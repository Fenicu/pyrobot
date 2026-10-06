// Типы окружения SvelteKit: https://svelte.dev/docs/kit/types#app.d.ts
declare global {
	namespace App {}
	/** Версия сборки (vite.config.ts `define`). */
	const __APP_VERSION__: string;
}

export {};
