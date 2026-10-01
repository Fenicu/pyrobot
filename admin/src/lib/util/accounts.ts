/** Состояние аккаунта и причины, по которым его движок не запущен, — для человека. */
export type AccountStatus = 'enabled' | 'disabled' | 'error' | 'deleting';

export const STATUS_LABEL: Record<AccountStatus, string> = {
	enabled: 'включён',
	disabled: 'выключен',
	error: 'ошибка',
	deleting: 'удаляется'
};

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
