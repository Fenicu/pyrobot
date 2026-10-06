import { VEHICLE } from '$lib/util/game';
import { SETTINGS_TEXT } from './help';

/** Подпись настройки по пути (`engine`, `lottery.tickets`, `engine.mode`); нет — заголовок схемы. */
export function settingLabel(path: string, fallback: string): string {
	return SETTINGS_TEXT[path]?.label ?? fallback;
}

/** Описание настройки по пути: что делает, единицы, когда применяется. */
export function settingHelp(path: string): string | undefined {
	return SETTINGS_TEXT[path]?.help;
}

const GADGET_SET: Record<string, string> = {
	summer: '🌞 Летний (T11)',
	autumn: '🍂 Осень (T12)',
	um: 'Um-сет (T13)',
	pig: '🐷 Свинтус (T14)'
};

const VALUE_LABELS: Record<string, Record<string, string>> = {
	'trips.vehicles': VEHICLE,
	'gadgets.sets': GADGET_SET
};

export function valueLabels(path: string): Record<string, string> | undefined {
	return VALUE_LABELS[path];
}

/** Порядок секций в меню: сначала то, что меняют чаще (механики, дела, сон, лотерея…), в конце —
 * «Дополнительно»; остальные — после, в порядке схемы. */
export const SECTION_ORDER = [
	'features',
	'strategy',
	'daily',
	'sleep',
	'lottery',
	'artifacts',
	'gadgets',
	'trips',
	'battle',
	'stocks',
	'metro',
	'food',
	'levelup',
	'tangerine',
	'chats',
	'engine'
];

/** «Дополнительно»: темп шлюза — ставят один раз. */
export const ADVANCED_SECTIONS = ['engine'];
