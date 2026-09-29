import type { JournalItem, JournalPage } from '$lib/api/types';
import { fixture } from './fixtures';

/** Запуски в ленте с прода 27.09 (фикстура снята до run_id): сон — решение 342 и шаги 472–474,
 * фастфуд — решение 340 и шаги 470–471; остальное — без запуска. */
const RUNS: Record<string, number> = {
	'decision:342': 90,
	'action:472': 90,
	'action:473': 90,
	'action:474': 90,
	'decision:340': 89,
	'action:470': 89,
	'action:471': 89
};

/** Страница журнала с прода, как её отдаёт сервер с `run_id`. */
export function journalWithRuns(): JournalPage {
	const page = fixture<JournalPage>('journal_page');
	return {
		...page,
		items: page.items.map(
			(i) => ({ ...i, ...(i.type === 'message' ? {} : { run_id: RUNS[`${i.type}:${i.id}`] ?? null }) }) as JournalItem
		)
	};
}
