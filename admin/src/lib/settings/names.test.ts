import { describe, expect, it } from 'vitest';
import type { SettingsOut } from '$lib/api/types';
import { fixture } from '$lib/test/fixtures';
import { settingNames } from './names';
import { sectionsOf, type JsonSchema } from './schema';

const nameOf = settingNames(sectionsOf(fixture<SettingsOut>('settings').schema as JsonSchema));

describe('подписи настроек по пути', () => {
	it('раздел и название вместо пути, вложенные группы — тоже', () => {
		expect(nameOf('sleep.duration_h')).toMatchObject({ section: 'Сон', label: 'Длительность сна, ч' });
		expect(nameOf('features.casino')).toMatchObject({ section: 'Функции', label: 'Казино' });
		expect(nameOf('lottery.keep.money').section).toBe('Лотерея');
		expect(nameOf('lottery.keep.money').field?.path).toEqual(['lottery', 'keep', 'money']);
	});

	it('пути нет в схеме — как есть, без поля', () => {
		expect(nameOf('engine.gone')).toEqual({ section: '', label: 'engine.gone', field: null });
	});
});
