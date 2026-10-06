import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

// Бэкенд для `npm run dev`: локальный pyrobot или прод (cookie сессии — того же origin, что Vite).
const api = process.env.PYROBOT_DEV_API ?? 'http://127.0.0.1:8080';
// Версия сборки — git-тег без «v» (CI передаёт его build-arg PYROBOT_VERSION); без него — dev.
const version = process.env.PYROBOT_VERSION || '0.0.0-dev';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit(), svelteTesting()],
	define: {
		__APP_VERSION__: JSON.stringify(version)
	},
	server: {
		proxy: {
			'/api': { target: api, changeOrigin: true, secure: true }
		},
		// CHANGES.rst — в корне репозитория. Файлом его не разрешить: `?raw` Vite сверяет с каталогами;
		// .env и .git закрывает штатный server.fs.deny.
		fs: { allow: ['..'] }
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		setupFiles: ['./vitest-setup.ts']
	}
});
