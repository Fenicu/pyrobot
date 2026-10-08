import type { TangerinePartnerOut } from '$lib/api/types';
import { accountHref } from '$lib/nav';

export interface PartnerLine {
	text: string;
	/** Свой аккаунт-адресат — ссылка на его главную. */
	href: string | null;
	bad: boolean;
}

/** Кому аккаунт дарит 🍊 (`GET /tangerine/partner`); null — показывать нечего: адресат не задан
 * или Telegram не спросить. */
export function partnerLine(out: TangerinePartnerOut | null): PartnerLine | null {
	if (out === null) return null;
	if (out.status === 'missing') return { text: '🍊 сообщение не найдено — дарить некуда', href: null, bad: true };
	if (out.status !== 'ok' || out.sender === null) return null;
	if (out.account) return { text: `🍊 обмен с ${out.account.name}`, href: accountHref(out.account.id, ''), bad: false };
	const { name, username } = out.sender;
	return { text: `🍊 дарим: ${name}${username ? ` (@${username})` : ''}`, href: null, bad: false };
}
