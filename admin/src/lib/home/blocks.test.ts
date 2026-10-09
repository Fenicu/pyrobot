import { describe, expect, it } from 'vitest';
import { BLOCK_IDS, BLOCK_TITLES } from './blocks';

describe('блоки главной', () => {
	it('стабильные id в порядке раскладки по умолчанию и заголовки', () => {
		expect(BLOCK_IDS).toEqual(['now', 'next', 'character', 'gadgets', 'today', 'daily', 'artifact']);
		expect(BLOCK_IDS.map((id) => BLOCK_TITLES[id])).toEqual([
			'Сейчас',
			'Дальше по времени',
			'Персонаж',
			'Гаджеты',
			'Сегодня',
			'Итоги дня',
			'Сбор артефакта'
		]);
	});
});
