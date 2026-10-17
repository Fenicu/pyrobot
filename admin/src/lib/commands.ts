import type { AccountApi } from '$lib/api/account';
import { call } from '$lib/api/client';
import { ApiFailure } from '$lib/api/errors';
import type { components } from '$lib/api/schema';
import type { CommandOut, ConfirmRequired, InlineButton } from '$lib/api/types';
import { dialogs } from '$lib/stores/confirm.svelte';
import { fmtTime } from '$lib/util/format';

/** Ключ идемпотентности: создаётся на клиенте один раз на отправку, повтор той же отправки — тот же
 * ключ (`[A-Za-z0-9_.:-]{1,64}`). */
export function newKey(prefix = 'ui'): string {
	const id =
		typeof crypto !== 'undefined' && 'randomUUID' in crypto
			? crypto.randomUUID()
			: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
	return `${prefix}-${id}`.slice(0, 64);
}

export type Confirmer = (confirm: ConfirmRequired, what: string) => Promise<boolean>;

/** Окно подтверждения risky: срок токена — 2 минуты, только для этих параметров и состояния. */
export const confirmRisky: Confirmer = (confirm, what) =>
	dialogs.confirm({
		title: `Подтвердите: «${what}»`,
		body:
			`Команда класса ${confirm.command_class} — может тратить ресурсы.\n` +
			`Подтверждение действует до ${fmtTime(confirm.expires_at, true)} и только для этих ` +
			'параметров и текущего состояния персонажа.',
		confirmText: 'Да, отправить',
		danger: true
	});

const MAX_CONFIRMS = 3;

/** Отправка ручной команды: 409 `confirm_required` → окно подтверждения → тот же запрос (тот же
 * ключ) с токеном. null — пользователь отменил. */
export async function withConfirm<B extends { confirm_token?: string | null }>(
	send: (body: B) => Promise<CommandOut>,
	body: B,
	what: string,
	confirmer: Confirmer = confirmRisky
): Promise<CommandOut | null> {
	let current = body;
	for (let i = 0; ; i++) {
		try {
			return await send(current);
		} catch (e) {
			if (!(e instanceof ApiFailure) || e.error.kind !== 'confirm' || i >= MAX_CONFIRMS) throw e;
			if (!(await confirmer(e.error.confirm, what))) return null;
			current = { ...current, confirm_token: e.error.confirm.confirm_token };
		}
	}
}

type SendIn = components['schemas']['SendIn'];
type ClickIn = components['schemas']['ClickIn'];

/** Текст в чат игры; повтор той же отправки — тот же `key`. */
export function sendText(api: AccountApi, text: string, key: string, confirmer?: Confirmer): Promise<CommandOut | null> {
	return withConfirm<SendIn>(
		(body) => call(api.POST('/commands/send', { body })),
		{ text, idempotency_key: key },
		text,
		confirmer
	);
}

/** Нажатие кнопки сообщения той ревизии, которую видно на экране; ключ — один на нажатие. */
export function clickButton(
	api: AccountApi,
	msg: { chat_id: number; msg_id: number; revision: number },
	button: InlineButton,
	confirmer?: Confirmer
): Promise<CommandOut | null> {
	const body: ClickIn = {
		chat_id: msg.chat_id,
		message_id: msg.msg_id,
		revision: msg.revision,
		callback_data: button[3] ?? '',
		idempotency_key: newKey('click')
	};
	return withConfirm<ClickIn>((b) => call(api.POST('/commands/click', { body: b })), body, `кнопка «${button[0]}»`, confirmer);
}

const REASON: Record<string, string> = {
	stale_revision: 'сообщение изменилось — обновите запись',
	stale_button: 'кнопки уже нет — сообщение изменилось',
	confirm_stale: 'подтверждение устарело — отправьте снова',
	dry_run: 'режим dry_run',
	killed: 'kill включён',
	paused: 'пауза',
	tg_unreachable: 'нет связи с Telegram',
	queue_full: 'очередь ручных команд полна — дождитесь поставленных',
	// Шлюз проверяет класс перед отправкой: управление могло кончиться, пока команда ждала очереди.
	forbidden: 'команда запрещена для бота — возьмите управление на экране «Игра», чтобы отправить вручную',
	donate: 'донат-команды бот не отправляет — возьмите управление на экране «Игра», чтобы отправить вручную'
};

export function isStale(out: CommandOut): boolean {
	return out.reason === 'stale_revision' || out.reason === 'stale_button';
}

/** Итог команды для всплывающего сообщения. */
function reasonText(reason: string): string {
	if (reason.startsWith('blocked:')) {
		const block = reason.slice('blocked:'.length);
		return block === 'reconcile_required' ? 'траты заблокированы до сверки' : `траты заблокированы: ${block}`;
	}
	return REASON[reason] ?? reason;
}

export function commandResult(out: CommandOut): { text: string; kind: 'ok' | 'warn' | 'error' | 'info' } {
	const why = reasonText(out.reason);
	switch (out.status) {
		case 'pending':
			return { text: 'Команда ещё исполняется — итог появится в журнале', kind: 'info' };
		case 'confirmed':
			return { text: `Выполнено${why ? `: ${why}` : ''}`, kind: 'ok' };
		case 'refused':
			return { text: `Отказ игры: ${why}`, kind: 'warn' };
		case 'suppressed':
			return { text: `Не отправлено: ${why}`, kind: 'warn' };
		case 'outcome_unknown':
			return { text: `Исход неизвестен: ${why}`, kind: 'warn' };
		default:
			return { text: `Отклонено: ${why}`, kind: 'error' };
	}
}
