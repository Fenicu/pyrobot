import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

// Встроенный скрипт app.html ставит тему до отрисовки (без мелькания тёмной при светлой).
const html = readFileSync(join(process.cwd(), 'src', 'app.html'), 'utf-8');
const boot = /<script>([\s\S]*?)<\/script>/.exec(html)?.[1] ?? '';

function run(saved: string | null, prefersLight = false): string {
	document.documentElement.className = '';
	localStorage.clear();
	if (saved !== null) localStorage.setItem('pyrobot.theme', saved);
	vi.stubGlobal('matchMedia', (q: string) => ({ matches: prefersLight && q === '(prefers-color-scheme: light)' }));
	new Function(boot)();
	return document.documentElement.className;
}

describe('тема до отрисовки', () => {
	afterEach(() => vi.unstubAllGlobals());

	it('app.html не ставит тему жёстко, скрипт — до стилей и модулей', () => {
		expect(html).not.toMatch(/<html[^>]*class=/);
		expect(boot).toContain('pyrobot.theme');
		expect(html.indexOf('<script>')).toBeLessThan(html.indexOf('%sveltekit.head%'));
	});

	it('выбор из localStorage: тёмная по умолчанию, светлая, как в системе', () => {
		expect(run(null)).toBe('dark');
		expect(run('light')).toBe('light');
		expect(run('dark', true)).toBe('dark');
		expect(run('system', true)).toBe('light');
		expect(run('system', false)).toBe('dark');
		expect(run('garbage', true)).toBe('dark');
	});
});
