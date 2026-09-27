import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Тесты запускаются из каталога admin (npm run test).
const DIR = join(process.cwd(), 'src', 'lib', 'fixtures');

/** Реальные ответы API и кадры SSE с прода (27.09). */
export function fixture<T = unknown>(name: string): T {
	return JSON.parse(fixtureText(`${name}.json`)) as T;
}

export function fixtureText(file: string): string {
	return readFileSync(join(DIR, file), 'utf-8');
}
