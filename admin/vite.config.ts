import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';

// Бэкенд для `npm run dev`: локальный pyrobot или прод (cookie сессии — того же origin, что Vite).
const api = process.env.PYROBOT_DEV_API ?? 'http://127.0.0.1:8080';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit(), svelteTesting()],
	server: {
		proxy: {
			'/api': { target: api, changeOrigin: true, secure: true }
		}
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts'],
		setupFiles: ['./vitest-setup.ts']
	}
});
