import { describe, expect, it } from 'vitest';
import schemaJson from './settings.schema.json';
import { SETTINGS_TEXT } from './help';
import { fieldsOf, pathKey, type Field, type JsonSchema } from './schema';

const root = schemaJson as JsonSchema;

/** Все пути схемы: корневые секции, вложенные группы (`lottery.tickets`) и листья. */
function schemaPaths(): { groups: string[]; leaves: string[] } {
	const groups: string[] = [];
	const leaves: string[] = [];
	const walk = (fields: Field[]) => {
		for (const f of fields) {
			if (f.type.kind === 'group') {
				groups.push(pathKey(f.path));
				walk(f.type.fields);
			} else {
				leaves.push(pathKey(f.path));
			}
		}
	};
	walk(fieldsOf(root, root));
	return { groups, leaves };
}

describe('описания настроек', () => {
	const { groups, leaves } = schemaPaths();

	it('схема: 17 секций и групп, 91 лист', () => {
		expect(groups).toHaveLength(17);
		expect(groups).toContain('lottery.tickets');
		expect(groups).toContain('lottery.keep');
		expect(groups).toContain('strategy.reserve_ahead_min');
		expect(leaves).toHaveLength(91);
	});

	it('словарь совпадает с путями схемы в обе стороны', () => {
		expect(Object.keys(SETTINGS_TEXT).sort()).toEqual([...groups, ...leaves].sort());
	});

	it('у каждого пути — подпись и описание', () => {
		for (const [path, text] of Object.entries(SETTINGS_TEXT)) {
			expect(text.label.trim(), path).not.toBe('');
			expect(text.help.trim().length, path).toBeGreaterThan(20);
		}
	});
});
