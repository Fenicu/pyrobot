import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

// Сборка у любого человека: в lock-файле только npmjs. Свой реестр npm ci подставит сам
// (replace-registry-host), а npm install через него записывает новые пакеты с его адресом.
const PUBLIC = 'https://registry.npmjs.org/';
const FIX = `sed -i 's#"resolved": "<свой реестр>/#"resolved": "${PUBLIC}#' package-lock.json`;

type Entry = { resolved?: string };

describe('package-lock.json', () => {
	it('все пакеты — с публичного npmjs, без адресов своего реестра', () => {
		const lock = JSON.parse(readFileSync(join(process.cwd(), 'package-lock.json'), 'utf-8'));
		const entries = Object.entries(lock.packages as Record<string, Entry>).filter(
			([, p]) => p.resolved !== undefined
		);
		expect(entries.length).toBeGreaterThan(100);
		const foreign = entries
			.filter(([, p]) => !p.resolved?.startsWith(PUBLIC))
			.map(([name, p]) => `${name} ${p.resolved}`);
		expect(foreign, `вернуть на npmjs: ${FIX}`).toEqual([]);
	});
});
