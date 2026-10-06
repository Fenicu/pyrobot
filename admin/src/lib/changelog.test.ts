import { describe, expect, it } from 'vitest';
import { compareVersions, entriesSince, parseChangelog, type ChangelogEntry } from './changelog';

const SAMPLE = `
История изменений
=================

Вводный абзац, который в записи попадать не должен.

0.2.0 — 10.09.2026
------------------

Добавлено
~~~~~~~~~

- Первый пункт.
- Пункт, который не поместился в одну строку и
  продолжается с отступом.

Исправлено
~~~~~~~~~~

- Что-то починили.

0.1.10 — 03.09.2026
-------------------

Изменено
~~~~~~~~

- Что-то поменяли.

0.1.9 — 01.09.2026
------------------

Удалено
~~~~~~~

- Что-то убрали.

0.1.0 — 31.08.2026
------------------

Начало истории изменений.
`;

const versions = (entries: ChangelogEntry[]) => entries.map((e) => e.version);

describe('разбор CHANGES.rst', () => {
	it('версии, даты и разделы', () => {
		const entries = parseChangelog(SAMPLE);
		expect(versions(entries)).toEqual(['0.2.0', '0.1.10', '0.1.9', '0.1.0']);
		expect(entries[0]?.date).toBe('10.09.2026');
		expect(entries[0]?.sections.map((s) => s.title)).toEqual(['Добавлено', 'Исправлено']);
	});

	it('пункт с переносом склеивается в одну строку', () => {
		expect(parseChangelog(SAMPLE)[0]?.sections[0]?.items).toEqual([
			'Первый пункт.',
			'Пункт, который не поместился в одну строку и продолжается с отступом.'
		]);
	});

	it('запись без разделов хранит свой текст', () => {
		const first = parseChangelog(SAMPLE).find((e) => e.version === '0.1.0');
		expect(first?.sections).toEqual([]);
		expect(first?.preamble).toEqual(['Начало истории изменений.']);
	});

	it('вводная часть файла не попадает в записи', () => {
		for (const entry of parseChangelog(SAMPLE)) {
			expect(entry.preamble.join(' ')).not.toContain('Вводный абзац');
		}
	});

	it('настоящий CHANGES.rst: свежая версия первой, даты ДД.ММ.ГГГГ, у каждой записи есть пункты', () => {
		const entries = parseChangelog();
		expect(entries.length).toBeGreaterThan(0);
		for (const [i, entry] of entries.entries()) {
			expect(entry.version).toMatch(/^\d+\.\d+\.\d+$/);
			expect(entry.date).toMatch(/^\d{2}\.\d{2}\.\d{4}$/);
			expect(entry.sections.flatMap((s) => s.items).length).toBeGreaterThan(0);
			const next = entries[i + 1];
			if (next) expect(compareVersions(entry.version, next.version)).toBeGreaterThan(0);
		}
	});
});

describe('сравнение версий', () => {
	it('посегментно числами, а не строками', () => {
		expect(compareVersions('0.10.0', '0.9.0')).toBeGreaterThan(0);
		expect(compareVersions('0.1.9', '0.1.10')).toBeLessThan(0);
		expect(compareVersions('0.18.0', '0.18.0')).toBe(0);
		expect(compareVersions('0.18', '0.18.0')).toBe(0);
	});
});

describe('что показать после обновления', () => {
	const entries = parseChangelog(SAMPLE);

	it('всё между виденной версией и установленной, свежая первой', () => {
		expect(versions(entriesSince('0.1.0', '0.2.0', entries))).toEqual(['0.2.0', '0.1.10', '0.1.9']);
	});

	it('версии новее установленной не показываются', () => {
		expect(versions(entriesSince('0.1.0', '0.1.10', entries))).toEqual(['0.1.10', '0.1.9']);
	});

	it('уже на установленной версии — ничего', () => {
		expect(entriesSince('0.2.0', '0.2.0', entries)).toEqual([]);
	});

	it('первый визит (виденной версии нет) — ничего', () => {
		expect(entriesSince(null, '0.2.0', entries)).toEqual([]);
	});

	it('после отката сервиса (виденная новее установленной) — ничего', () => {
		expect(entriesSince('0.3.0', '0.2.0', entries)).toEqual([]);
	});
});
