<script lang="ts">
	import Power from '@lucide/svelte/icons/power';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { EngineStatus } from '$lib/api/types';
	import { reasonText } from '$lib/util/accounts';

	interface Props {
		status: EngineStatus;
		accountId: number;
		/** Глобальный клиент: включение — `PATCH /accounts/{id}`. */
		api: Api;
		/** После включения: перечитать статус движка и список аккаунтов. */
		onchange: () => void;
		/** Аккаунт заблокирован владельцем сервера: включить нельзя, причина — в плашке. */
		blocked?: boolean;
		blockedReason?: string | null;
	}
	let { status, accountId, api, onchange, blocked = false, blockedReason = null }: Props = $props();
	let busy = $state(false);
	let error = $state('');

	// Без причины в данных — по состоянию аккаунта: включённый, но не запущенный, ещё стартует.
	const WITHOUT_REASON = {
		enabled: 'запускается',
		disabled: 'аккаунт выключен',
		error: 'ошибка',
		deleting: 'аккаунт удаляется'
	} as const;
	const code = $derived(status.status_reason ?? status.host_reason);
	// У заблокированного — причина блокировки; `blocked_by_owner` без блокировки устарел
	// (разблокировали) — тогда причина обычная по состоянию аккаунта.
	const reason = $derived(
		blocked
			? `заблокирован владельцем сервера${blockedReason ? `: ${blockedReason}` : ''}`
			: code === 'blocked_by_owner'
				? WITHOUT_REASON[status.status]
				: code
					? reasonText(code)
					: WITHOUT_REASON[status.status]
	);
	const canEnable = $derived(!blocked && (status.status === 'disabled' || status.status === 'error'));

	async function enable() {
		busy = true;
		error = '';
		try {
			await call(
				api.PATCH('/api/v1/accounts/{account_id}', {
					params: { path: { account_id: accountId } },
					body: { enabled: true }
				})
			);
			onchange();
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
		} finally {
			busy = false;
		}
	}
</script>

{#if !status.running}
	<section class="card mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm" role="status">
		<p class="ext-text min-w-0 flex-1 font-medium">Движок не запущен: {reason}</p>
		{#if canEnable}
			<button type="button" class="btn btn-primary" disabled={busy} onclick={enable}>
				<Power class="size-4" aria-hidden="true" /> Включить
			</button>
		{/if}
		{#if error}<p class="ext-text w-full text-warn-fg" role="alert">{error}</p>{/if}
	</section>
{/if}
