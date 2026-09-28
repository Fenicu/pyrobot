import { SETTINGS_TEXT } from './help';

/** Подпись настройки по пути (`engine`, `lottery.tickets`, `engine.mode`); нет — заголовок схемы. */
export function settingLabel(path: string, fallback: string): string {
	return SETTINGS_TEXT[path]?.label ?? fallback;
}

/** Описание настройки по пути: что делает, единицы, когда применяется. */
export function settingHelp(path: string): string | undefined {
	return SETTINGS_TEXT[path]?.help;
}

/** Порядок секций в меню (как в согласованном макете); остальные — после, в порядке схемы. */
export const SECTION_ORDER = [
	'engine',
	'features',
	'strategy',
	'daily',
	'lottery',
	'battle',
	'stocks',
	'sleep',
	'metro',
	'food',
	'levelup',
	'tangerine',
	'chats',
	'telegram',
	'retention'
];
