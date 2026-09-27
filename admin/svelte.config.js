import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
	preprocess: vitePreprocess(),
	compilerOptions: {
		// Руны везде, кроме библиотек из node_modules.
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		// SPA за FastAPI: единственная страница index.html, маршруты — на клиенте.
		adapter: adapter({ fallback: 'index.html' }),
		prerender: { entries: [] },
		// Ключ версии сборки не нужен: SPA не опрашивает _app/version.json.
		version: { pollInterval: 0 }
	}
};
