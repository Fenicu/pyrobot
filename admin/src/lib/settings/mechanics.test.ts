import { describe, expect, it } from 'vitest';
import schemaJson from './settings.schema.json';
import { GROUPS, OTHER_CARD_ID, cardAbout, cardOf, changeLabel, firstSentence, placeFields } from './mechanics';
import { editable, leaves, pathKey, sectionsOf, type Field, type JsonSchema, type Section } from './schema';

const sections = sectionsOf(schemaJson as JsonSchema);
const editableLeaves = sections.flatMap((s) => leaves(editable(s.fields))).map((f) => pathKey(f.path));
const cards = GROUPS.flatMap((g) => g.cards);

const leaf = (path: string, readOnly = false): Field => ({
	name: path.split('.').at(-1)!,
	path: path.split('.'),
	title: path,
	readOnly,
	unused: false,
	type: { kind: 'boolean' }
});

describe('механики настроек', () => {
	it('группы — по порядку, «Дополнительно» последней и помечена', () => {
		expect(GROUPS.map((g) => g.title)).toEqual([
			'Дела и прокачка',
			'Еда и сон',
			'Битва и деньги',
			'Метро и поездки',
			'Подарки и предметы',
			'Задания дня',
			'Мандарины и чаты',
			'Прочее',
			'Дополнительно'
		]);
		expect(GROUPS.filter((g) => g.advanced).map((g) => g.title)).toEqual(['Дополнительно']);
		const advanced = GROUPS.at(-1)!.cards.map((c) => c.title);
		expect(advanced).toEqual(['Движок', 'Прочие настройки']);
		expect(GROUPS.at(-1)!.cards.at(-1)!.id).toBe(OTHER_CARD_ID);
	});

	it('id карточек и групп уникальны, у каждой карточки есть включатель или поля', () => {
		const ids = cards.map((c) => c.id);
		expect(new Set(ids).size).toBe(ids.length);
		expect(new Set(GROUPS.map((g) => g.id)).size).toBe(GROUPS.length);
		for (const c of cards) {
			if (c.id === OTHER_CARD_ID) continue;
			expect(c.feature !== undefined || c.paths.length > 0, c.id).toBe(true);
			expect(c.title, c.id).not.toBe('');
			expect(c.icon, c.id).not.toBe('');
		}
	});

	it('покрытие схемы: каждый редактируемый лист — ровно в одной карточке, «Прочие настройки» пусты', () => {
		const owners = new Map<string, string[]>();
		for (const c of cards) {
			for (const p of [...(c.feature ? [c.feature] : []), ...c.paths]) {
				owners.set(p, [...(owners.get(p) ?? []), c.id]);
			}
		}
		const missing = editableLeaves.filter((p) => !owners.has(p));
		const twice = [...owners].filter(([, ids]) => ids.length > 1);
		expect(missing).toEqual([]);
		expect(twice).toEqual([]);
		expect(placeFields(sections).get(OTHER_CARD_ID)).toEqual([]);
	});

	it('в таблице нет путей, которых нет среди редактируемых листьев схемы', () => {
		const known = new Set(editableLeaves);
		const listed = cards.flatMap((c) => [...(c.feature ? [c.feature] : []), ...c.paths]);
		expect(listed.filter((p) => !known.has(p))).toEqual([]);
	});

	it('включатель — флаг features.*, а в полях карточки его нет', () => {
		for (const c of cards) {
			if (c.feature) expect(c.feature, c.id).toMatch(/^features\./);
		}
		const placed = placeFields(sections);
		const all = [...placed.values()].flat().map((f) => pathKey(f.path));
		expect(all.some((p) => p.startsWith('features.'))).toBe(false);
		expect(placed.get('metro')!.map((f) => pathKey(f.path))).toEqual(cardOf('metro.buffs')!.card.paths);
	});

	it('поле, которого нет в таблице (сервер новее), попадает в «Прочие настройки»; «только чтение» — никуда', () => {
		const extra: Section[] = [
			...sections.map((s) =>
				s.name === 'metro' ? { ...s, fields: [...s.fields, leaf('metro.new_knob')] } : s
			),
			{ name: 'casino', title: 'Casino', fields: [leaf('casino.bet'), leaf('casino.secret', true)] }
		];
		const placed = placeFields(extra);
		expect(placed.get(OTHER_CARD_ID)!.map((f) => pathKey(f.path))).toEqual(['metro.new_knob', 'casino.bet']);
		expect(placed.get('metro')!.map((f) => pathKey(f.path))).not.toContain('metro.new_knob');
	});

	it('новый флаг features.* (сервер новее) — обычное поле в «Прочих настройках»', () => {
		const extra = sections.map((s) =>
			s.name === 'features' ? { ...s, fields: [...s.fields, leaf('features.new_flag')] } : s
		);
		const placed = placeFields(extra);
		expect(placed.get(OTHER_CARD_ID)!.map((f) => pathKey(f.path))).toEqual(['features.new_flag']);
		expect(cardOf('features.new_flag')).toBeNull();
	});

	it('cardOf: поле и включатель — в своей карточке, неизвестный путь — null', () => {
		const hit = cardOf('metro.buffs');
		expect(hit?.card.title).toBe('Метро');
		expect(hit?.group.title).toBe('Метро и поездки');
		expect(cardOf('features.metro')?.card.id).toBe('metro');
		expect(cardOf('strategy.reserve_ahead_min.metro')?.card.id).toBe('metro');
		expect(cardOf('strategy.reserve_ahead_min.gorbushka')?.card.id).toBe('gorbushka');
		expect(cardOf('chats.tangerine_reply_to')?.card.id).toBe('tangerine');
		expect(cardOf('chats.team_chat_id')?.card.id).toBe('chats');
		// Каналы смузи и биржевиков — в карточках своих механик, флаг защиты от ограбления — в «Битве и деньгах».
		expect(cardOf('chats.smoothie_channel_id')?.card.id).toBe('smoothie');
		expect(cardOf('chats.bulls_invite_chat_id')?.card.id).toBe('bulls');
		expect(cardOf('features.robbery_defense')?.group.title).toBe('Битва и деньги');
		expect(cardOf('features.tangerine_gifts')?.group.title).toBe('Подарки и предметы');
		expect(cardOf('engine.mode')?.group.advanced).toBe(true);
		expect(cardOf('metro.gone')).toBeNull();
	});

	it('changeLabel: карточка и подпись поля; включатель — «Включено»; неизвестный путь — запасные подписи', () => {
		expect(changeLabel('strategy.reserve_ahead_min.metro', 'Стратегия и дела', 'Metro')).toEqual({
			section: 'Метро',
			label: 'Запас 🔥 под вход в метро, мин'
		});
		expect(changeLabel('features.metro', 'Функции', 'Метро')).toEqual({ section: 'Метро', label: 'Включено' });
		expect(changeLabel('metro.new_knob', 'Метро', 'New Knob')).toEqual({ section: 'Метро', label: 'New Knob' });
	});

	it('описание карточки: справка флага, без флага — справка секции, у «Прочих» — своё', () => {
		const card = (id: string) => cards.find((c) => c.id === id)!;
		expect(cardAbout(card('metro'))).toMatch(/^Забеги в метро/);
		expect(cardAbout(card('engine'))).toMatch(/^Режим и темп шлюза/);
		expect(cardAbout(card('chats'))).toMatch(/^Чаты и каналы/);
		expect(cardAbout(card(OTHER_CARD_ID))).toMatch(/эта версия админки/);
	});

	it('firstSentence: до первой точки перед новым предложением', () => {
		expect(firstSentence('Раз. Два.')).toBe('Раз.');
		expect(firstSentence('Ночью (22:00–08:00 МСК) жать. Встретив — драться.')).toBe('Ночью (22:00–08:00 МСК) жать.');
		expect(firstSentence('Одно предложение')).toBe('Одно предложение');
		expect(firstSentence('Сумма 1.5 мин. Дальше.')).toBe('Сумма 1.5 мин.');
		// Точка в скобках — не конец предложения.
		expect(firstSentence('Очки навыков (см. «Практика». Теория) делить. Дальше.')).toBe(
			'Очки навыков (см. «Практика». Теория) делить.'
		);
	});
});
