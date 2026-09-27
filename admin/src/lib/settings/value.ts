/** Значение настройки для показа (умолчание, история): текстом. */
export function fmtValue(value: unknown): string {
	if (value === null || value === undefined) return 'не задано';
	if (value === true) return 'вкл';
	if (value === false) return 'выкл';
	if (Array.isArray(value)) return value.length ? value.map((v) => fmtValue(v)).join(', ') : '—';
	if (typeof value === 'object') {
		const entries = Object.entries(value as Record<string, unknown>);
		return entries.length ? entries.map(([k, v]) => `${k}: ${fmtValue(v)}`).join(', ') : '—';
	}
	return String(value);
}
