import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

function svelteFiles(dir: string): string[] {
	return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
		e.isDirectory() ? svelteFiles(join(dir, e.name)) : e.name.endsWith('.svelte') ? [join(dir, e.name)] : []
	);
}

describe('внешний текст только текстом', () => {
	it('{@html} не используется нигде в src', () => {
		const files = svelteFiles(SRC);
		expect(files.length).toBeGreaterThan(5);
		const offenders = files.filter((f) => /\{@html\b/.test(readFileSync(f, 'utf-8')));
		expect(offenders).toEqual([]);
	});

	it('innerHTML не присваивается', () => {
		const offenders = svelteFiles(SRC).filter((f) => /\.innerHTML\s*=/.test(readFileSync(f, 'utf-8')));
		expect(offenders).toEqual([]);
	});
});
