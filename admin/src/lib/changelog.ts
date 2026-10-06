// История изменений — тот же CHANGES.rst из корня репозитория, что читают в git: вторая копия для
// админки разъехалась бы с ним.
import raw from '../../../CHANGES.rst?raw';

export interface ChangelogSection {
	/** «Добавлено», «Изменено», «Исправлено», «Удалено». */
	title: string;
	items: string[];
}

export interface ChangelogEntry {
	version: string;
	/** ДД.ММ.ГГГГ; пусто, если даты нет. */
	date: string;
	sections: ChangelogSection[];
	/** Текст записи вне разделов. */
	preamble: string[];
}

/** `0.10.0` новее `0.9.0`: посегментно числами, а не строками. */
export function compareVersions(a: string, b: string): number {
	const pa = a.split('.').map((n) => Number.parseInt(n, 10) || 0);
	const pb = b.split('.').map((n) => Number.parseInt(n, 10) || 0);
	for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
		const diff = (pa[i] ?? 0) - (pb[i] ?? 0);
		if (diff !== 0) return diff;
	}
	return 0;
}

const VERSION_LINE = /^(\d+\.\d+(?:\.\d+)?)(?:\s+—\s+(.+))?$/;

function underline(line: string | undefined, char: string): boolean {
	const s = line?.trim() ?? '';
	return s.length >= 3 && s === char.repeat(s.length);
}

/** Подмножество reStructuredText: версия подчёркнута `-`, раздел — `~`, пункт начинается с `- ` и
 * продолжается строками с отступом. Текст до первой версии — правила для автора, не запись. */
export function parseChangelog(text: string = raw): ChangelogEntry[] {
	const lines = text.split('\n');
	const entries: ChangelogEntry[] = [];
	let entry: ChangelogEntry | null = null;
	let section: ChangelogSection | null = null;
	let bullet: string[] | null = null;

	const flush = () => {
		if (!bullet) return;
		const item = bullet.join(' ').replace(/\s+/g, ' ').trim();
		if (item) (section ? section.items : entry?.preamble)?.push(item);
		bullet = null;
	};

	for (let i = 0; i < lines.length; i++) {
		const line = lines[i] ?? '';
		const next = lines[i + 1];
		const version = VERSION_LINE.exec(line.trim());
		if (version && underline(next, '-')) {
			flush();
			entry = { version: version[1] ?? '', date: version[2]?.trim() ?? '', sections: [], preamble: [] };
			section = null;
			entries.push(entry);
			i++;
			continue;
		}
		if (!entry) continue;
		if (line.trim() && underline(next, '~')) {
			flush();
			section = { title: line.trim(), items: [] };
			entry.sections.push(section);
			i++;
			continue;
		}
		if (line.startsWith('- ')) {
			flush();
			bullet = [line.slice(2)];
		} else if (bullet && line.startsWith('  ') && line.trim()) {
			bullet.push(line);
		} else if (!line.trim()) {
			flush();
		} else if (!section && !line.startsWith('..')) {
			entry.preamble.push(line.trim());
		}
	}
	flush();
	return entries;
}

/** Записи новее виденной версии `seen` и не новее установленной `current`, свежая первой. Виденной
 * версии нет (первый визит) — ничего: окно рассказывает об обновлениях, а не обо всей истории. */
export function entriesSince(
	seen: string | null,
	current: string,
	entries: ChangelogEntry[] = parseChangelog()
): ChangelogEntry[] {
	if (!seen || compareVersions(seen, current) >= 0) return [];
	return entries
		.filter((e) => compareVersions(e.version, seen) > 0 && compareVersions(e.version, current) <= 0)
		.sort((a, b) => compareVersions(b.version, a.version));
}
