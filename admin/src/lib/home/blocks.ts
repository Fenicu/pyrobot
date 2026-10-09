/** Блоки главной аккаунта: стабильные id (они же — в сохранённой раскладке) и заголовки. */
export type BlockId = 'now' | 'next' | 'character' | 'gadgets' | 'today' | 'daily' | 'artifact';

/** Порядок раскладки по умолчанию: колонки слева направо, в колонке — сверху вниз. */
export const BLOCK_IDS: readonly BlockId[] = ['now', 'next', 'character', 'gadgets', 'today', 'daily', 'artifact'];

export const BLOCK_TITLES: Record<BlockId, string> = {
	now: 'Сейчас',
	next: 'Дальше по времени',
	character: 'Персонаж',
	gadgets: 'Гаджеты',
	today: 'Сегодня',
	daily: 'Итоги дня',
	artifact: 'Сбор артефакта'
};
