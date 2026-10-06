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

	it('схема: 21 секция и группа, 119 листьев', () => {
		expect(groups).toHaveLength(21);
		expect(groups).toContain('lottery.tickets');
		expect(groups).toContain('lottery.keep');
		expect(groups).toContain('strategy.reserve_ahead_min');
		expect(groups).toContain('artifacts');
		expect(groups).toContain('artifact_run');
		expect(groups).toContain('trips');
		expect(groups).toContain('gadgets');
		expect(groups).toContain('gadget_upgrade');
		expect(leaves).toContain('features.team_pick');
		expect(leaves).toHaveLength(119);
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

describe('описания гаджетов', () => {
	it('пустой список сетов не выключает пустые слоты и замену', () => {
		const sets = SETTINGS_TEXT['gadgets.sets']?.help;
		expect(sets).not.toContain('ничего не покупать');
		expect(sets).toContain('пустые слоты');
		const flag = SETTINGS_TEXT['features.gadgets_buy']?.help;
		for (const rule of ['Пустой слот', 'Части сетов', 'Когда копить не на что']) {
			expect(flag).toContain(rule);
		}
	});

	it('«авто»: ⚪️ до уровня, дальше 🔴, кончились — 🔵', () => {
		const help = SETTINGS_TEXT['gadgets.white_until']?.help;
		expect(help).toContain('дальше — 🔴, а когда они кончатся — 🔵');
	});
});
