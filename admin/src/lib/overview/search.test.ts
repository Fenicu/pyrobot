import { describe, expect, it } from 'vitest';
import type { SearchHitOut } from '$lib/api/types';
import {
	formatSearchParams,
	groupHits,
	normalizeFirstLine,
	parseSearchParams,
	reasonText,
	SEARCH_REASONS,
	validateQuery,
	type SearchQuery
} from './search';

const hit = (msg_id: number, text: string | null, over: Partial<SearchHitOut> = {}): SearchHitOut => ({
	account_id: 1,
	chat_id: 100,
	msg_id,
	date: '2026-10-17T12:00:00Z',
	outgoing: false,
	text,
	markup: null,
	...over
});

describe('search pure logic', () => {
	describe('normalizeFirstLine', () => {
		it('заменяет числа на N, обрезает пробелы и оставляет первую строку', () => {
			expect(normalizeFirstLine('Ты получил 500 монет!\nОсталось 10')).toBe('Ты получил N монет!');
			expect(normalizeFirstLine('  надел 2 маски и 10 перчаток  ')).toBe('надел N маски и N перчаток');
		});

		it('для пустого текста или null возвращает заглушку', () => {
			expect(normalizeFirstLine(null)).toBe('(без текста)');
			expect(normalizeFirstLine('')).toBe('(без текста)');
			expect(normalizeFirstLine('   \n  ')).toBe('(без текста)');
		});

		it('обрезает длину до 100 символов', () => {
			const long = 'Слово '.repeat(30);
			const norm = normalizeFirstLine(long);
			expect(norm.length).toBeLessThanOrEqual(100);
		});
	});

	describe('groupHits', () => {
		it('группирует совпадения по нормализованной первой строке и сортирует по убыванию количества', () => {
			const hits: SearchHitOut[] = [
				hit(1, 'Ты получил 100 монет!'),
				hit(2, 'Редкое событие'),
				hit(3, 'Ты получил 500 монет!'),
				hit(4, 'Ты получил 9999 монет!')
			];
			const groups = groupHits(hits);
			expect(groups).toHaveLength(2);
			expect(groups[0]!.count).toBe(3);
			expect(groups[0]!.key).toBe('Ты получил N монет!');
			expect(groups[0]!.sample).toBe('Ты получил 100 монет!');
			expect(groups[0]!.hits.map((h) => h.msg_id)).toEqual([1, 3, 4]);

			expect(groups[1]!.count).toBe(1);
			expect(groups[1]!.key).toBe('Редкое событие');
			expect(groups[1]!.sample).toBe('Редкое событие');
		});

		it('для пустого списка возвращает пустой массив групп', () => {
			expect(groupHits([])).toEqual([]);
		});
	});

	describe('reasonText', () => {
		it('переводит все коды причин бекенда на русский язык', () => {
			expect(reasonText('engine_not_running')).toBe('движок не запущен');
			expect(reasonText('locked_elsewhere')).toBe('запущен на другом сервере');
			expect(reasonText('tg_offline')).toBe('Telegram не в сети');
			expect(reasonText('tg_unreachable')).toBe('Telegram недоступен');
			expect(reasonText('timeout')).toBe('таймаут ожидания');
			expect(reasonText('flood_wait')).toBe('ограничение Telegram (flood wait)');
			expect(reasonText('chat_unavailable')).toBe('чат игры недоступен');
			expect(reasonText('internal_error')).toBe('внутренняя ошибка');
		});

		it('для незнакомой причины возвращает её саму, для null/undefined — пустую строку', () => {
			expect(reasonText('unknown_err')).toBe('unknown_err');
			expect(reasonText(null)).toBe('');
			expect(reasonText(undefined)).toBe('');
		});
	});

	describe('parseSearchParams and formatSearchParams', () => {
		it('разбирает валидные параметры URL', () => {
			const params = new URLSearchParams('q=маска&where=telegram&accounts=1,2&limit=100');
			const parsed = parseSearchParams(params);
			expect(parsed).toEqual({
				q: 'маска',
				where: 'telegram',
				accountIds: [1, 2],
				limit: 100
			});
		});

		it('подставляет значения по умолчанию при отсутствии параметров', () => {
			const parsed = parseSearchParams(new URLSearchParams(''));
			expect(parsed).toEqual({
				q: '',
				where: 'db',
				accountIds: [],
				limit: 50
			});
		});

		it('игнорирует некорректные значения where, limit и id аккаунтов', () => {
			const parsed = parseSearchParams(new URLSearchParams('where=unknown&accounts=abc,-5,2&limit=999'));
			expect(parsed.where).toBe('db');
			expect(parsed.accountIds).toEqual([2]);
			expect(parsed.limit).toBe(50);
		});

		it('форматирует параметры поиска в URLSearchParams', () => {
			const query: SearchQuery = {
				q: 'маска',
				where: 'telegram',
				accountIds: [1, 2],
				limit: 100
			};
			const sp = formatSearchParams(query);
			expect(sp.get('q')).toBe('маска');
			expect(sp.get('where')).toBe('telegram');
			expect(sp.get('accounts')).toBe('1,2');
			expect(sp.get('limit')).toBe('100');
		});

		it('не включает дефолтный limit и пустые accounts в search params', () => {
			const sp = formatSearchParams({
				q: 'тест',
				where: 'db',
				accountIds: [],
				limit: 50
			});
			expect(sp.get('q')).toBe('тест');
			expect(sp.get('where')).toBe('db');
			expect(sp.has('accounts')).toBe(false);
			expect(sp.has('limit')).toBe(false);
		});
	});

	describe('validateQuery', () => {
		it('возвращает null для корректной длины 2..100', () => {
			expect(validateQuery('маска')).toBeNull();
			expect(validateQuery('  две буквы  ')).toBeNull();
			expect(validateQuery('x'.repeat(100))).toBeNull();
		});

		it('возвращает ошибку для слишком короткой или пустой строки', () => {
			expect(validateQuery('')).toBe('Введите не менее 2 символов');
			expect(validateQuery(' ')).toBe('Введите не менее 2 символов');
			expect(validateQuery('а')).toBe('Введите не менее 2 символов');
		});

		it('возвращает ошибку для строки длиннее 100 символов', () => {
			expect(validateQuery('x'.repeat(101))).toBe('Не более 100 символов');
		});
	});
});
