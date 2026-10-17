import type { SearchHitOut } from '$lib/api/types';

export const SEARCH_REASONS: Record<string, string> = {
	engine_not_running: 'движок не запущен',
	locked_elsewhere: 'запущен на другом сервере',
	tg_offline: 'Telegram не в сети',
	tg_unreachable: 'Telegram недоступен',
	timeout: 'таймаут ожидания',
	flood_wait: 'ограничение Telegram (flood wait)',
	chat_unavailable: 'чат игры недоступен',
	internal_error: 'внутренняя ошибка'
};

/** Человекочитаемый текст причины ошибки или недоступности аккаунта. */
export function reasonText(reason: string | null | undefined): string {
	if (!reason) return '';
	return SEARCH_REASONS[reason] ?? reason;
}

/** Нормализация первой строки для группировки: числа заменяются на N, длина ограничена ~100 символами. */
export function normalizeFirstLine(text: string | null | undefined): string {
	if (!text) return '(без текста)';
	const firstLine = text.split(/\r?\n/)[0]?.trim() ?? '';
	if (!firstLine) return '(без текста)';
	const normalized = firstLine.replace(/\d+/g, 'N');
	return normalized.slice(0, 100);
}

export interface SearchGroup {
	key: string;
	sample: string;
	count: number;
	hits: SearchHitOut[];
}

/** Группировка совпадений по нормализованной первой строке с сортировкой по убыванию количества. */
export function groupHits(hits: SearchHitOut[]): SearchGroup[] {
	const map = new Map<string, { sample: string; hits: SearchHitOut[] }>();
	for (const h of hits) {
		const key = normalizeFirstLine(h.text);
		let entry = map.get(key);
		if (!entry) {
			const rawFirstLine = (h.text ?? '').split(/\r?\n/)[0]?.trim() || '(без текста)';
			entry = { sample: rawFirstLine.slice(0, 100), hits: [] };
			map.set(key, entry);
		}
		entry.hits.push(h);
	}
	return Array.from(map.entries())
		.map(([key, { sample, hits }]) => ({
			key,
			sample,
			count: hits.length,
			hits
		}))
		.sort((a, b) => b.count - a.count);
}

export interface SearchQuery {
	q: string;
	where: 'db' | 'telegram';
	accountIds: number[];
	limit: number;
}

export const DEFAULT_SEARCH_QUERY: SearchQuery = {
	q: '',
	where: 'db',
	accountIds: [],
	limit: 50
};

/** Разбор параметров поиска из URL. */
export function parseSearchParams(searchParams: URLSearchParams): SearchQuery {
	const q = (searchParams.get('q') ?? '').trim();
	const rawWhere = searchParams.get('where');
	const where: 'db' | 'telegram' = rawWhere === 'telegram' ? 'telegram' : 'db';
	const accountsRaw = searchParams.get('accounts');
	const accountIds: number[] = [];
	if (accountsRaw) {
		for (const part of accountsRaw.split(',')) {
			const n = Number(part.trim());
			if (Number.isInteger(n) && n > 0 && !accountIds.includes(n)) {
				accountIds.push(n);
			}
		}
		accountIds.sort((a, b) => a - b);
	}
	const rawLimit = Number(searchParams.get('limit'));
	const limit = Number.isInteger(rawLimit) && rawLimit >= 1 && rawLimit <= 200 ? rawLimit : 50;
	return { q, where, accountIds, limit };
}

/** Преобразование параметров поиска в URLSearchParams для адресной строки. */
export function formatSearchParams(query: SearchQuery): URLSearchParams {
	const sp = new URLSearchParams();
	if (query.q) sp.set('q', query.q.trim());
	sp.set('where', query.where);
	if (query.accountIds.length > 0) {
		sp.set('accounts', query.accountIds.join(','));
	}
	if (query.limit !== 50) {
		sp.set('limit', String(query.limit));
	}
	return sp;
}

/** Клиентская валидация поискового запроса: от 2 до 100 символов. */
export function validateQuery(q: string): string | null {
	const trimmed = q.trim();
	if (trimmed.length < 2) return 'Введите не менее 2 символов';
	if (trimmed.length > 100) return 'Не более 100 символов';
	return null;
}
