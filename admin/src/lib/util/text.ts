/** Первая непустая строка внешнего текста — для строк ленты. */
export function firstLine(text: string | null | undefined, max = 140): string {
	const line = (text ?? '').split('\n').find((l) => l.trim() !== '') ?? '';
	return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** JSON для показа как текст. */
export function pretty(value: unknown): string {
	return JSON.stringify(value, null, 2);
}
