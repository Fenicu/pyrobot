<script lang="ts">
	import UserPlus from '@lucide/svelte/icons/user-plus';
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure, errorText, type ApiError } from '$lib/api/errors';
	import type { EngineStatus } from '$lib/api/types';

	interface Props {
		status: EngineStatus;
		api: AccountApi;
		/** После вступления: перечитать статус движка. */
		onchange: () => void;
	}
	let { status, api, onchange }: Props = $props();
	let busy = $state(false);
	let error = $state('');
	let requested = $state(false);
	// Вступил: плашка скрыта до следующего статуса движка — он и решает, показывать ли её снова.
	let joinedAt = $state.raw<EngineStatus | null>(null);

	const ERRORS: Record<string, string> = {
		game_chat_mismatch: 'Чат @startupwarschat не совпадает с чатом SWINFO из настроек — вступление отменено',
		tg_not_online: 'Telegram не в онлайне — сначала подключите аккаунт',
		join_declined: 'Заявку на вступление отклонили'
	};

	function joinError(err: ApiError): string {
		if (err.kind === 'rate_limited' && err.code === 'flood_wait') {
			return err.retryAfter !== null ? `Telegram просит подождать ${err.retryAfter} с` : 'Telegram просит подождать';
		}
		if ('code' in err && ERRORS[err.code]) return ERRORS[err.code]!;
		if (err.kind === 'unavailable' && err.status === 502 && err.code) return `Telegram отказал: ${err.code}`;
		return errorText(err);
	}

	async function join() {
		busy = true;
		error = '';
		try {
			const out = await call(api.POST('/tg/game-chat/join'));
			if (out.status === 'request_sent') {
				requested = true;
			} else {
				joinedAt = status;
				onchange();
			}
		} catch (e) {
			error = e instanceof ApiFailure ? joinError(e.error) : String(e);
		} finally {
			busy = false;
		}
	}
</script>

{#if status.game_chat_member === false && joinedAt !== status}
	<section class="card mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm" role="status">
		<p class="ext-text min-w-0 flex-1 font-medium">
			Аккаунт не состоит в общем чате игры @startupwarschat — бот не видит SWINFO: анонсы фабрики, лотерею, цены
			акций.
		</p>
		{#if requested}
			<p class="ext-text w-full text-warn-fg">Заявка отправлена, ждёт одобрения в чате</p>
		{:else}
			<button type="button" class="btn btn-primary" disabled={busy} onclick={join}>
				<UserPlus class="size-4" aria-hidden="true" />
				{busy ? 'Вступаю…' : 'Вступить в общий чат игры'}
			</button>
		{/if}
		{#if error}<p class="ext-text w-full text-warn-fg" role="alert">{error}</p>{/if}
	</section>
{/if}
