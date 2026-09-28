import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');
const SELF = join(SRC, 'lib', 'no-html.test.ts');

function sourceFiles(dir: string): string[] {
	return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
		e.isDirectory()
			? sourceFiles(join(dir, e.name))
			: /\.(svelte|ts|js)$/.test(e.name)
				? [join(dir, e.name)]
				: []
	);
}

// Вставка HTML строкой: {@html}, присваивание innerHTML/outerHTML (= и +=), insertAdjacentHTML().
const SINKS = [/\{@html\b/, /\.(?:inner|outer)HTML\s*\+?=(?!=)/, /\binsertAdjacentHTML\s*\(/];

const offends = (text: string) => SINKS.some((re) => re.test(text));

describe('внешний текст только текстом', () => {
	it('проверка ловит все способы вставить HTML строкой', () => {
		for (const bad of [
			'{@html item.text}',
			'el.innerHTML = text;',
			'el.innerHTML+=text',
			'node.outerHTML = `<b>${x}</b>`',
			"el.insertAdjacentHTML('beforeend', text)"
		]) {
			expect(offends(bad), bad).toBe(true);
		}
		for (const ok of ['if (el.innerHTML === "") {}', 'const html = el.outerHTML;', 'el.textContent = text;']) {
			expect(offends(ok), ok).toBe(false);
		}
	});

	it('в src нет {@html}, innerHTML/outerHTML = и insertAdjacentHTML — ни в .svelte, ни в .ts/.js', () => {
		const files = sourceFiles(SRC).filter((f) => f !== SELF);
		expect(files.filter((f) => f.endsWith('.svelte')).length).toBeGreaterThan(5);
		expect(files.filter((f) => f.endsWith('.ts')).length).toBeGreaterThan(5);
		const offenders = files.filter((f) => offends(readFileSync(f, 'utf-8')));
		expect(offenders).toEqual([]);
	});
});
