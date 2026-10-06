import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseChangelog } from '$lib/changelog';
import { SEEN_VERSION_KEY, WhatsNew } from './whatsnew.svelte';

const entries = parseChangelog(`
0.18.0 — 07.10.2026
-------------------

Добавлено
~~~~~~~~~

- Гаджеты.

0.17.3 — 06.10.2026
-------------------

Добавлено
~~~~~~~~~

- Вход.

0.3.0 — 03.10.2026
------------------

Добавлено
~~~~~~~~~

- Третье.

0.2.0 — 02.10.2026
------------------

Исправлено
~~~~~~~~~~

- Второе.

0.1.0 — 01.10.2026
------------------

Добавлено
~~~~~~~~~

- Первое.
`);

afterEach(() => {
	localStorage.clear();
	vi.restoreAllMocks();
});

describe('окно «Что нового»', () => {
	it('первый визит: виденной считается 0.17.3 — окно с тем, что вышло после неё', () => {
		const w = new WhatsNew('0.18.0', entries);
		w.check();
		expect(w.open).toBe(true);
		expect(w.entries.map((e) => e.version)).toEqual(['0.18.0']);
		expect(localStorage.getItem(SEEN_VERSION_KEY)).toBe('0.18.0');
	});

	it('первый визит на версии до окна — окна нет, версия запомнена', () => {
		const w = new WhatsNew('0.3.0', entries);
		w.check();
		expect(w.open).toBe(false);
		expect(localStorage.getItem(SEEN_VERSION_KEY)).toBe('0.3.0');
	});

	it('та же версия ещё раз — окна нет', () => {
		localStorage.setItem(SEEN_VERSION_KEY, '0.3.0');
		const w = new WhatsNew('0.3.0', entries);
		w.check();
		expect(w.open).toBe(false);
	});

	it('после обновления — пропущенные версии, свежая первой', () => {
		localStorage.setItem(SEEN_VERSION_KEY, '0.1.0');
		const w = new WhatsNew('0.3.0', entries);
		w.check();
		expect(w.open).toBe(true);
		expect(w.entries.map((e) => e.version)).toEqual(['0.3.0', '0.2.0']);
	});

	it('версия отмечается виденной сразу: после перезагрузки окно не вернётся', () => {
		localStorage.setItem(SEEN_VERSION_KEY, '0.1.0');
		new WhatsNew('0.3.0', entries).check();
		expect(localStorage.getItem(SEEN_VERSION_KEY)).toBe('0.3.0');
		const again = new WhatsNew('0.3.0', entries);
		again.check();
		expect(again.open).toBe(false);
	});

	it('«Понятно» закрывает окно', () => {
		localStorage.setItem(SEEN_VERSION_KEY, '0.1.0');
		const w = new WhatsNew('0.3.0', entries);
		w.check();
		w.dismiss();
		expect(w.open).toBe(false);
	});

	it('localStorage недоступен — окна нет и ничего не падает', () => {
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('SecurityError');
		});
		vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
			throw new Error('SecurityError');
		});
		const w = new WhatsNew('0.3.0', entries);
		expect(() => w.check()).not.toThrow();
		expect(w.open).toBe(false);
	});

	it('сборка без тега (0.0.0-dev) — окна нет и виденная версия не меняется', () => {
		localStorage.setItem(SEEN_VERSION_KEY, '0.1.0');
		const w = new WhatsNew('0.0.0-dev', entries);
		w.check();
		expect(w.open).toBe(false);
		expect(localStorage.getItem(SEEN_VERSION_KEY)).toBe('0.1.0');
	});
});
