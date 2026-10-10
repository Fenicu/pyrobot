/** Список по сохранённому порядку id: аккаунты не из порядка (новые) — в конце в своём порядке,
 * id, которых больше нет, пропускаются. Пустой порядок — список как есть. */
export function applyOrder<T extends { id: number }>(list: T[], ids: readonly number[] | null): T[] {
	if (!ids || ids.length === 0) return list;
	const rank = new Map(ids.map((id, i) => [id, i]));
	const known = list.filter((a) => rank.has(a.id)).sort((a, b) => rank.get(a.id)! - rank.get(b.id)!);
	return [...known, ...list.filter((a) => !rank.has(a.id))];
}

/** Копия с элементом `from`, переставленным на место `to`; за краями — без изменений. */
export function moveItem<T>(list: readonly T[], from: number, to: number): T[] {
	const out = [...list];
	if (from === to || from < 0 || to < 0 || from >= out.length || to >= out.length) return out;
	const [item] = out.splice(from, 1);
	out.splice(to, 0, item!);
	return out;
}
