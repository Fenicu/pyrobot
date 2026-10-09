<script lang="ts">
	import Ban from '@lucide/svelte/icons/ban';
	import CheckCircle2 from '@lucide/svelte/icons/check-circle-2';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import type { AdminAccountOut } from '$lib/api/types';
	import Modal from '$lib/components/Modal.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { fmtMoment, fmtNum } from '$lib/util/format';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();

	let blocking = $state<AdminAccountOut | null>(null);
	let blockReason = $state('');
	let blockError = $state('');

	let removing = $state<AdminAccountOut | null>(null);
	let confirmName = $state('');
	let removeError = $state('');

	let actionBusy = $state(false);

	onMount(() => {
		if (!store.accounts) {
			void store.loadAccounts();
		}
	});

	function startBlock(a: AdminAccountOut) {
		blocking = a;
		blockReason = '';
		blockError = '';
	}

	async function confirmBlock(e: SubmitEvent) {
		e.preventDefault();
		const trimmed = blockReason.trim();
		if (!blocking || !trimmed) return;
		actionBusy = true;
		blockError = '';
		try {
			await store.patchAccount(blocking.id, true, trimmed);
			toasts.show(`Аккаунт ${blocking.name} заблокирован`, 'ok');
			blocking = null;
		} catch (err: unknown) {
			blockError = err instanceof Error ? err.message : String(err);
		} finally {
			actionBusy = false;
		}
	}

	async function unblock(a: AdminAccountOut) {
		actionBusy = true;
		try {
			await store.patchAccount(a.id, false);
			toasts.show(`Аккаунт ${a.name} разблокирован`, 'ok');
		} catch (err: unknown) {
			toasts.show(err instanceof Error ? err.message : String(err), 'error');
		} finally {
			actionBusy = false;
		}
	}

	async function restart(a: AdminAccountOut) {
		actionBusy = true;
		try {
			await store.restartAccount(a.id);
			toasts.show(`Перезапуск аккаунта ${a.name} отправлен`, 'ok');
		} catch (err: unknown) {
			toasts.show(err instanceof Error ? err.message : String(err), 'error');
		} finally {
			actionBusy = false;
		}
	}

	function startRemove(a: AdminAccountOut) {
		removing = a;
		confirmName = '';
		removeError = '';
	}

	async function confirmDelete(e: SubmitEvent) {
		e.preventDefault();
		if (!removing || confirmName !== removing.name) return;
		actionBusy = true;
		removeError = '';
		try {
			await store.deleteAccount(removing.id, confirmName);
			toasts.show(`Аккаунт ${removing.name} удаляется`, 'ok');
			removing = null;
		} catch (err: unknown) {
			removeError = err instanceof Error ? err.message : String(err);
		} finally {
			actionBusy = false;
		}
	}

	function formatRows(rows: Record<string, number> | undefined): string {
		if (!rows) return '—';
		return Object.entries(rows)
			.map(([k, v]) => `${k}: ${fmtNum(v)}`)
			.join(', ');
	}
</script>

<div class="space-y-[14px]">
	{#if store.accountsError}
		<p class="card text-sm text-bad-fg" role="alert">{store.accountsError}</p>
	{/if}

	{#if store.accountsLoading && !store.accounts}
		<p class="text-sm text-fg-muted" role="status">Загрузка аккаунтов…</p>
	{:else if store.accounts && store.accounts.length === 0}
		<p class="text-sm text-fg-muted">Аккаунтов нет.</p>
	{:else if store.accounts}
		<div
			role="table"
			aria-label="Аккаунты сервера"
			class="space-y-2 xl:space-y-0 xl:overflow-hidden xl:rounded-lg xl:border xl:border-line xl:bg-surface"
		>
			<div
				role="row"
				class="hidden gap-3 border-b border-line-soft px-3 py-2 text-xs font-semibold tracking-wide text-fg-muted uppercase xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,1.4fr)_minmax(0,0.9fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_9rem]"
			>
				<span role="columnheader">Владелец</span>
				<span role="columnheader">Аккаунт</span>
				<span role="columnheader">Статус / Блок</span>
				<span role="columnheader">Движок / TG</span>
				<span role="columnheader">Рестарты (24ч)</span>
				<span role="columnheader">Нагрузка (1ч)</span>
				<span role="columnheader">Строки БД</span>
				<span role="columnheader" class="sr-only">Действия</span>
			</div>

			{#each store.accounts as a (a.id)}
				<div
					role="row"
					data-testid="account-row-{a.id}"
					class="card grid gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,1.4fr)_minmax(0,0.9fr)_minmax(0,1.2fr)_minmax(0,1.2fr)_9rem] xl:items-center xl:gap-3 xl:rounded-none xl:border-0 xl:border-b xl:border-line-soft xl:last:border-b-0"
				>
					<div role="cell" class="min-w-0">
						<span class="text-xs text-fg-muted xl:hidden">Владелец: </span>
						<span class="font-mono text-sm font-medium">{a.owner_login ?? '—'}</span>
					</div>

					<div role="cell" class="min-w-0 font-medium">
						<span class="text-xs text-fg-muted xl:hidden">Аккаунт: </span>
						<span>{a.name}</span>
					</div>

					<div role="cell" class="space-y-1">
						<div class="flex flex-wrap items-center gap-1.5">
							<Pill
								tone={a.status === 'enabled'
									? 'ok'
									: a.status === 'error'
										? 'bad'
										: a.status === 'deleting'
											? 'warn'
											: 'muted'}
							>
								{a.status}
							</Pill>
							{#if a.blocked}
								<Pill tone="bad">заблокирован</Pill>
							{/if}
						</div>
						{#if a.blocked && a.blocked_reason}
							<p class="text-[11px] text-bad-fg">Блок: {a.blocked_reason}</p>
						{/if}
						{#if a.status_reason}
							<p class="text-[11px] text-fg-muted">{a.status_reason}</p>
						{/if}
					</div>

					<div role="cell" class="space-y-1">
						<div class="flex flex-wrap items-center gap-1.5">
							<Pill tone={a.running ? 'ok' : 'muted'}>
								{a.running ? 'движок запущен' : 'движок остановлен'}
							</Pill>
							<Pill tone={a.tg_online ? 'ok' : 'muted'}>
								{a.tg_online ? 'TG онлайн' : 'TG оффлайн'}
							</Pill>
						</div>
						{#if a.last_error_code}
							<p class="text-[11px] text-bad-fg font-mono">
								{a.last_error_code} {a.last_error_at ? `(${fmtMoment(a.last_error_at)})` : ''}
							</p>
						{/if}
					</div>

					<div role="cell" class="text-sm">
						<span class="text-xs text-fg-muted xl:hidden">Рестарты: </span>
						<span>{a.restarts_24h}</span>
					</div>

					<div role="cell" class="text-xs text-fg-muted">
						<span class="xl:hidden">Нагрузка: </span>
						<span>сообщ: {a.messages_1h}, действ: {a.actions_1h}</span>
					</div>

					<div role="cell" class="min-w-0 text-[11px] text-fg-faint" title={formatRows(a.rows)}>
						<span class="text-xs text-fg-muted xl:hidden">БД: </span>
						<span class="truncate block">{formatRows(a.rows)}</span>
					</div>

					<div role="cell" class="flex flex-wrap items-center gap-1.5 xl:justify-end">
						{#if a.status !== 'deleting'}
							{#if a.blocked}
								<button
									type="button"
									class="btn btn-ghost px-2 py-1 text-xs text-ok-fg"
									title="Разблокировать"
									onclick={() => unblock(a)}
									disabled={actionBusy}
								>
									<CheckCircle2 class="size-3.5" aria-hidden="true" />
									<span class="xl:sr-only">Разблокировать</span>
								</button>
							{:else}
								<button
									type="button"
									class="btn btn-ghost px-2 py-1 text-xs text-warn-fg"
									title="Заблокировать"
									onclick={() => startBlock(a)}
									disabled={actionBusy}
								>
									<Ban class="size-3.5" aria-hidden="true" />
									<span class="xl:sr-only">Заблокировать</span>
								</button>
							{/if}

							<button
								type="button"
								class="btn btn-ghost px-2 py-1 text-xs"
								title="Перезапустить"
								onclick={() => restart(a)}
								disabled={actionBusy || !a.running}
							>
								<RotateCw class="size-3.5" aria-hidden="true" />
								<span class="xl:sr-only">Перезапустить</span>
							</button>

							<button
								type="button"
								class="btn btn-ghost px-2 py-1 text-xs text-bad-fg"
								title="Удалить"
								onclick={() => startRemove(a)}
								disabled={actionBusy}
							>
								<Trash2 class="size-3.5" aria-hidden="true" />
								<span class="xl:sr-only">Удалить</span>
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if blocking}
	<Modal title="Заблокировать аккаунт?" onclose={() => (blocking = null)}>
		<form id="block-account-form" class="space-y-3" onsubmit={confirmBlock}>
			<p class="text-sm text-fg-muted">
				Аккаунт <span class="font-medium text-fg">{blocking.name}</span> будет заблокирован, его движок
				остановится.
			</p>
			<label class="block space-y-1">
				<span class="label">Причина блокировки (обязательно)</span>
				<input
					class="input"
					bind:value={blockReason}
					maxlength="256"
					required
					placeholder="Укажите причину блокировки"
					data-autofocus
				/>
			</label>
			{#if blockError}
				<p class="text-sm text-bad-fg" role="alert">{blockError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (blocking = null)}>Отмена</button>
			<button
				type="submit"
				form="block-account-form"
				class="btn btn-danger"
				disabled={actionBusy || !blockReason.trim()}
			>
				Заблокировать
			</button>
		{/snippet}
	</Modal>
{/if}

{#if removing}
	<Modal title="Удалить аккаунт?" onclose={() => (removing = null)}>
		<form id="delete-account-form" class="space-y-3" onsubmit={confirmDelete}>
			<p class="text-sm text-fg-muted">
				Аккаунт <span class="font-medium text-fg">{removing.name}</span> будет удалён навсегда со всеми
				данными. Действие необратимо.
			</p>
			<label class="block space-y-1">
				<span class="label">Введите точное имя аккаунта для подтверждения</span>
				<input
					class="input"
					bind:value={confirmName}
					placeholder={removing.name}
					autocomplete="off"
					data-autofocus
				/>
			</label>
			{#if removeError}
				<p class="text-sm text-bad-fg" role="alert">{removeError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (removing = null)}>Отмена</button>
			<button
				type="submit"
				form="delete-account-form"
				class="btn btn-danger"
				disabled={actionBusy || !removing || confirmName !== removing.name}
			>
				Удалить навсегда
			</button>
		{/snippet}
	</Modal>
{/if}
