// Причины, по которым движок аккаунта не запущен, — для человека.

// Причина host_reason: хост не смог захватить аккаунт.
const REASONS: Record<string, string> = {
	locked_elsewhere: 'аккаунт занят другим хостом',
	lease_active: 'ждёт окончания аренды прежнего хоста'
};

// Причины status_reason с подробностью после двоеточия (имя исключения).
const PREFIXED: [string, string][] = [
	['crash_loop:', 'движок несколько раз упал подряд'],
	['start_failed:', 'движок не запустился']
];

/** Причина как есть, если перевода нет. */
export function reasonText(code: string): string {
	const known = REASONS[code];
	if (known !== undefined) return known;
	for (const [prefix, text] of PREFIXED) {
		if (code.startsWith(prefix)) return `${text}: ${code.slice(prefix.length)}`;
	}
	return code;
}
