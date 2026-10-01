import { describe, expect, it } from 'vitest';
import { match } from './legacy';

describe('матчер старых ссылок', () => {
	it('старые разделы без аккаунта — да, нынешние маршруты и прочее — нет', () => {
		const old = ['journal', 'control', 'daily', 'metrics', 'metro', 'notifications', 'settings', 'telegram'];
		for (const path of old) expect(match(path), path).toBe(true);
		for (const path of ['accounts', 'login', 'password', 'a', '', 'journalx', 'Journal', 'a/1']) {
			expect(match(path), path).toBe(false);
		}
	});
});
