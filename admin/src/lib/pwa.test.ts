import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// Установка на главный экран телефона: манифест, иконки и ссылки на них в app.html.
const staticDir = join(process.cwd(), 'static');
const html = readFileSync(join(process.cwd(), 'src', 'app.html'), 'utf-8');

type Icon = { src: string; sizes: string; type: string; purpose?: string };

function manifest(): { [key: string]: unknown; icons: Icon[] } {
	return JSON.parse(readFileSync(join(staticDir, 'manifest.webmanifest'), 'utf-8'));
}

// Ширина и высота из заголовка IHDR: байты 16–23 PNG.
function pngSize(file: string): [number, number] {
	const buf = readFileSync(file);
	expect(buf.subarray(1, 4).toString('ascii')).toBe('PNG');
	return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

describe('manifest.webmanifest', () => {
	it('описывает отдельное приложение', () => {
		const m = manifest();
		expect(m.name).toBe('pyrobot');
		expect(m.short_name).toBe('pyrobot');
		expect(m.start_url).toBe('/');
		expect(m.scope).toBe('/');
		expect(m.display).toBe('standalone');
		expect(m).not.toHaveProperty('prefer_related_applications');
	});

	it('содержит иконки 192, 512 и maskable', () => {
		const icons = manifest().icons;
		const any = icons.filter((i) => (i.purpose ?? 'any').split(' ').includes('any'));
		expect(any.map((i) => i.sizes)).toEqual(expect.arrayContaining(['192x192', '512x512']));
		expect(icons.some((i) => i.purpose?.split(' ').includes('maskable'))).toBe(true);
	});

	it('каждая иконка лежит в static и её размер совпадает с заявленным', () => {
		for (const icon of manifest().icons) {
			const file = join(staticDir, icon.src.replace(/^\//, ''));
			expect(existsSync(file), icon.src).toBe(true);
			expect(icon.type).toBe('image/png');
			const [w, h] = icon.sizes.split('x').map(Number);
			expect(pngSize(file), icon.src).toEqual([w, h]);
		}
	});
});

describe('app.html', () => {
	it('ссылается на манифест', () => {
		expect(html).toContain('<link rel="manifest" href="%sveltekit.assets%/manifest.webmanifest" />');
	});

	it('ссылается на apple-touch-icon 180x180', () => {
		expect(html).toContain('<link rel="apple-touch-icon" href="%sveltekit.assets%/apple-touch-icon.png" />');
		expect(pngSize(join(staticDir, 'apple-touch-icon.png'))).toEqual([180, 180]);
	});

	it('задаёт theme-color для тёмной и светлой темы', () => {
		expect(html).toMatch(
			/<meta name="theme-color" media="\(prefers-color-scheme: dark\)" content="#111317" \/>/
		);
		expect(html).toMatch(
			/<meta name="theme-color" media="\(prefers-color-scheme: light\)" content="#f4f5f7" \/>/
		);
	});
});
