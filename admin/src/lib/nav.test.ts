import { describe, expect, it } from 'vitest';
import { loginHref, safeNext } from './nav';

describe('возврат после входа', () => {
	it('вход запоминает текущую страницу', () => {
		expect(loginHref(new URL('https://sw.example/journal?type=action#x'))).toBe(
			'/login?next=%2Fjournal%3Ftype%3Daction%23x'
		);
		expect(loginHref(new URL('https://sw.example/'))).toBe('/login');
		expect(loginHref(new URL('https://sw.example/login?next=%2Fmetro'))).toBe('/login?next=%2Fmetro');
		// После смены пароля — вход без возврата на форму смены.
		expect(loginHref(new URL('https://sw.example/password'))).toBe('/login');
	});

	it('возврат — только на путь этого приложения', () => {
		expect(safeNext('/metro?run=3')).toBe('/metro?run=3');
		expect(safeNext('/settings#engine')).toBe('/settings#engine');
		for (const bad of [
			null,
			'',
			'metro',
			'https://evil.example/',
			'//evil.example/x',
			'/\\evil.example',
			'/\t/evil.example',
			'javascript:alert(1)',
			'/.//evil.example',
			'/login',
			'/login?next=%2Fmetro'
		]) {
			expect(safeNext(bad), String(bad)).toBe('/');
		}
	});
});
