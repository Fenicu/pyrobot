import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	accountHref,
	homeHref,
	isActive,
	isPublic,
	lastAccount,
	legacyHref,
	loginHref,
	mainNav,
	moreNav,
	parseAccount,
	rememberAccount,
	safeNext,
	switchHref
} from './nav';

const url = (path: string) => new URL(`https://sw.example${path}`);

afterEach(() => {
	vi.restoreAllMocks();
	localStorage.clear();
});

describe('возврат после входа', () => {
	it('вход запоминает текущую страницу', () => {
		expect(loginHref(url('/a/1/journal?type=action#x'))).toBe('/login?next=%2Fa%2F1%2Fjournal%3Ftype%3Daction%23x');
		expect(loginHref(url('/'))).toBe('/login');
		expect(loginHref(url('/login?next=%2Fmetro'))).toBe('/login?next=%2Fmetro');
		// После смены пароля — вход без возврата на форму смены; список аккаунтов — и так рядом.
		expect(loginHref(url('/password'))).toBe('/login');
		expect(loginHref(url('/accounts'))).toBe('/login');
	});

	it('возврат — только на путь этого приложения', () => {
		expect(safeNext('/a/1/metro?run=3')).toBe('/a/1/metro?run=3');
		expect(safeNext('/a/1/settings#engine')).toBe('/a/1/settings#engine');
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

	it('публичные пути: вход, восстановление, приглашение', () => {
		expect(isPublic('/login')).toBe(true);
		expect(isPublic('/recover')).toBe(true);
		expect(isPublic('/invite/token123')).toBe(true);
		expect(isPublic('/invite/32-byte-token_abc-def')).toBe(true);

		expect(isPublic('/')).toBe(false);
		expect(isPublic('/accounts')).toBe(false);
		expect(isPublic('/password')).toBe(false);
		expect(isPublic('/invite')).toBe(false);
		expect(isPublic('/invite/')).toBe(false);
		expect(isPublic('/recover/step')).toBe(false);

		const item = moreNav(null).find((i) => i.href === '/password');
		expect(item?.label).toBe('Пароль и коды');
	});
});

describe('экраны аккаунта', () => {
	it('accountHref и isActive с префиксом', () => {
		expect(accountHref(3, '')).toBe('/a/3');
		expect(accountHref(3, '/journal')).toBe('/a/3/journal');
		expect(mainNav(3).map((i) => i.href)).toEqual(['/a/3', '/a/3/journal', '/a/3/control', '/a/3/metro']);
		expect(moreNav(3).map((i) => i.href)).toEqual([
			'/a/3/daily',
			'/a/3/metrics',
			'/a/3/settings',
			'/a/3/notifications',
			'/a/3/telegram',
			'/accounts',
			'/password'
		]);
		// Без аккаунта — только общие пункты.
		expect(moreNav(null).map((i) => i.href)).toEqual(['/accounts', '/password']);

		// Главная аккаунта — только сама, разделы — со вложенными путями.
		expect(isActive('/a/3', '/a/3')).toBe(true);
		expect(isActive('/a/3/journal', '/a/3')).toBe(false);
		expect(isActive('/a/31', '/a/3')).toBe(false);
		expect(isActive('/a/3/journal', '/a/3/journal')).toBe(true);
		expect(isActive('/a/3/journal/x', '/a/3/journal')).toBe(true);
		expect(isActive('/a/3/journalx', '/a/3/journal')).toBe(false);
		expect(isActive('/a/31/journal', '/a/3/journal')).toBe(false);
		expect(isActive('/password', '/password')).toBe(true);

		// Переключение — тот же раздел другого аккаунта, без query: номера забегов у каждого свои.
		expect(switchHref('/a/3/metro', 5)).toBe('/a/5/metro');
		expect(switchHref('/a/3', 5)).toBe('/a/5');
		expect(switchHref('/password', 5)).toBe('/a/5');
		expect(switchHref('/accounts', 5)).toBe('/a/5');

		expect(parseAccount('7')).toBe(7);
		for (const bad of [undefined, '', 'x', '0', '07', '-1', '1.5', '1e3', '99999999999999999999']) {
			expect(parseAccount(bad), String(bad)).toBeNull();
		}
	});

	it('старые пути ведут в последний аккаунт с тем же search', () => {
		const list = [{ id: 2 }, { id: 5 }];
		rememberAccount(5);
		expect(lastAccount()).toBe(5);
		expect(homeHref(list, lastAccount())).toBe('/a/5');
		// Последнего уже нет — первый из списка; аккаунтов нет — их список.
		expect(homeHref(list, 9)).toBe('/a/2');
		expect(homeHref(list, null)).toBe('/a/2');
		expect(homeHref([], 5)).toBe('/accounts');

		const old = ['/journal', '/control', '/daily', '/metrics', '/metro', '/notifications', '/settings', '/telegram'];
		for (const path of old) expect(legacyHref(url(path), 5), path).toBe(`/a/5${path}`);
		expect(legacyHref(url('/metro?run=12'), 5)).toBe('/a/5/metro?run=12');
		expect(legacyHref(url('/journal?type=action#x'), 5)).toBe('/a/5/journal?type=action#x');
		expect(legacyHref(url('/journal'), null)).toBe('/accounts');
		for (const now of ['/', '/a/5/journal', '/login', '/password', '/accounts', '/journal/x']) {
			expect(legacyHref(url(now), 5), now).toBeNull();
		}
	});

	it('последний аккаунт без localStorage — нет, но и без ошибок', () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new DOMException('denied', 'SecurityError');
		});
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new DOMException('denied', 'SecurityError');
		});
		expect(() => rememberAccount(5)).not.toThrow();
		expect(lastAccount()).toBeNull();
		vi.restoreAllMocks();
		localStorage.setItem('pyrobot.account', 'мусор');
		expect(lastAccount()).toBeNull();
	});
});
