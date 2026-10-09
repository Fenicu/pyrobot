<script lang="ts">
	import Pencil from '@lucide/svelte/icons/pencil';
	import Power from '@lucide/svelte/icons/power';
	import PowerOff from '@lucide/svelte/icons/power-off';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import type { AdminUserOut } from '$lib/api/types';
	import Modal from '$lib/components/Modal.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();

	let editingLimit = $state<AdminUserOut | null>(null);
	let newLimit = $state<number | null>(1);
	let limitError = $state('');

	let disabling = $state<AdminUserOut | null>(null);
	let disableReason = $state('');
	let disableError = $state('');

	let deleting = $state<AdminUserOut | null>(null);
	let confirmLogin = $state('');
	let deleteError = $state('');

	let actionBusy = $state(false);

	onMount(() => {
		if (!store.users) {
			void store.loadUsers();
		}
	});

	function startEditLimit(u: AdminUserOut) {
		editingLimit = u;
		newLimit = u.max_accounts;
		limitError = '';
	}

	async function saveLimit(e: SubmitEvent) {
		e.preventDefault();
		if (!editingLimit) return;
		if (newLimit == null || Number.isNaN(newLimit) || !Number.isInteger(newLimit) || newLimit < 1 || newLimit > 1000) {
			limitError = 'Лимит должен быть целым числом от 1 до 1000';
			return;
		}
		actionBusy = true;
		limitError = '';
		try {
			await store.patchUser(editingLimit.id, { max_accounts: newLimit });
			toasts.show(`Лимит для ${editingLimit.login} изменён на ${newLimit}`, 'ok');
			editingLimit = null;
		} catch (err: unknown) {
			limitError = err instanceof Error ? err.message : String(err);
		} finally {
			actionBusy = false;
		}
	}

	function startDisable(u: AdminUserOut) {
		disabling = u;
		disableReason = '';
		disableError = '';
	}

	async function confirmDisable(e: SubmitEvent) {
		e.preventDefault();
		if (!disabling) return;
		actionBusy = true;
		disableError = '';
		try {
			await store.patchUser(disabling.id, {
				disabled: true,
				reason: disableReason.trim() ? disableReason.trim() : null
			});
			toasts.show(`Пользователь ${disabling.login} отключён`, 'ok');
			disabling = null;
		} catch (err: unknown) {
			disableError = err instanceof Error ? err.message : String(err);
		} finally {
			actionBusy = false;
		}
	}

	async function enableUser(u: AdminUserOut) {
		actionBusy = true;
		try {
			await store.patchUser(u.id, { disabled: false });
			toasts.show(`Пользователь ${u.login} включён`, 'ok');
		} catch (err: unknown) {
			toasts.show(err instanceof Error ? err.message : String(err), 'error');
		} finally {
			actionBusy = false;
		}
	}

	function startDelete(u: AdminUserOut) {
		deleting = u;
		confirmLogin = '';
		deleteError = '';
	}

	async function confirmDeleteUser(e: SubmitEvent) {
		e.preventDefault();
		if (!deleting || confirmLogin !== deleting.login) return;
		actionBusy = true;
		deleteError = '';
		try {
			await store.deleteUser(deleting.id, confirmLogin);
			toasts.show(`Пользователь ${deleting.login} удаляется`, 'ok');
			deleting = null;
		} catch (err: unknown) {
			deleteError = err instanceof Error ? err.message : String(err);
		} finally {
			actionBusy = false;
		}
	}
</script>

<div class="space-y-[14px]">
	{#if store.usersError}
		<p class="card text-sm text-bad-fg" role="alert">{store.usersError}</p>
	{/if}

	{#if store.usersLoading && !store.users}
		<p class="text-sm text-fg-muted" role="status">Загрузка пользователей…</p>
	{:else if store.users && store.users.length === 0}
		<p class="text-sm text-fg-muted">Пользователей нет.</p>
	{:else if store.users}
		<div
			role="table"
			aria-label="Пользователи"
			class="space-y-2 xl:space-y-0 xl:overflow-hidden xl:rounded-lg xl:border xl:border-line xl:bg-surface"
		>
			<div
				role="row"
				class="hidden gap-3 border-b border-line-soft px-3 py-2 text-xs font-semibold tracking-wide text-fg-muted uppercase xl:grid xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,1fr)_8rem]"
			>
				<span role="columnheader">Логин</span>
				<span role="columnheader">Роль</span>
				<span role="columnheader">Заведён</span>
				<span role="columnheader">Последний вход</span>
				<span role="columnheader">Аккаунты</span>
				<span role="columnheader">Статус</span>
				<span role="columnheader" class="sr-only">Действия</span>
			</div>

			{#each store.users as u (u.id)}
				<div
					role="row"
					data-testid="user-row-{u.id}"
					class="card grid gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,0.8fr)_minmax(0,1fr)_8rem] xl:items-center xl:gap-3 xl:rounded-none xl:border-0 xl:border-b xl:border-line-soft xl:last:border-b-0"
				>
					<div role="cell" class="min-w-0 font-medium">
						<span class="font-mono">{u.login}</span>
					</div>

					<div role="cell">
						<Pill tone={u.role === 'owner' ? 'ok' : 'muted'}>
							{u.role === 'owner' ? 'владелец' : 'пользователь'}
						</Pill>
					</div>

					<div role="cell" class="text-xs text-fg-muted">
						{fmtMoment(u.created_at)}
					</div>

					<div role="cell" class="text-xs text-fg-muted">
						{u.last_login_at ? fmtMoment(u.last_login_at) : '—'}
					</div>

					<div role="cell" class="text-sm">
						{u.accounts} из {u.max_accounts}
					</div>

					<div role="cell">
						{#if u.deleting}
							<Pill tone="warn">удаляется</Pill>
						{:else if u.disabled}
							<Pill tone="bad">отключён</Pill>
							{#if u.disabled_reason}
								<span class="mt-0.5 block text-[11px] text-fg-muted">{u.disabled_reason}</span>
							{/if}
						{:else}
							<Pill tone="ok">активен</Pill>
						{/if}
					</div>

					<div role="cell" class="flex flex-wrap items-center gap-1.5 xl:justify-end">
						{#if !u.deleting}
							<button
								type="button"
								class="btn btn-ghost px-2 py-1 text-xs"
								title="Изменить лимит"
								onclick={() => startEditLimit(u)}
								disabled={actionBusy}
							>
								<Pencil class="size-3.5" aria-hidden="true" />
								<span class="xl:sr-only">Лимит</span>
							</button>

							{#if u.disabled}
								<button
									type="button"
									class="btn btn-ghost px-2 py-1 text-xs"
									title="Включить"
									onclick={() => enableUser(u)}
									disabled={actionBusy}
								>
									<Power class="size-3.5" aria-hidden="true" />
									<span class="xl:sr-only">Включить</span>
								</button>
							{:else}
								<button
									type="button"
									class="btn btn-ghost px-2 py-1 text-xs text-warn-fg"
									title="Отключить"
									onclick={() => startDisable(u)}
									disabled={actionBusy}
								>
									<PowerOff class="size-3.5" aria-hidden="true" />
									<span class="xl:sr-only">Отключить</span>
								</button>
							{/if}

							<button
								type="button"
								class="btn btn-ghost px-2 py-1 text-xs text-bad-fg"
								title="Удалить"
								onclick={() => startDelete(u)}
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

{#if editingLimit}
	<Modal title="Лимит аккаунтов" onclose={() => (editingLimit = null)}>
		<form id="limit-form" class="space-y-3" onsubmit={saveLimit} novalidate>
			<p class="text-sm text-fg-muted">
				Пользователь: <span class="font-mono font-medium text-fg">{editingLimit.login}</span>
			</p>
			<label class="block space-y-1">
				<span class="label">Максимум аккаунтов (1..1000)</span>
				<input
					type="number"
					step="any"
					class="input"
					bind:value={newLimit}
					min="1"
					max="1000"
					required
					data-autofocus
				/>
			</label>
			{#if limitError}
				<p class="text-sm text-bad-fg" role="alert">{limitError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (editingLimit = null)}>Отмена</button>
			<button
				type="submit"
				form="limit-form"
				class="btn btn-primary"
				disabled={actionBusy || newLimit == null || newLimit < 1 || newLimit > 1000}
			>
				Сохранить
			</button>
		{/snippet}
	</Modal>
{/if}

{#if disabling}
	<Modal title="Отключить пользователя?" onclose={() => (disabling = null)}>
		<form id="disable-form" class="space-y-3" onsubmit={confirmDisable}>
			<p class="text-sm text-fg-muted">
				Все сессии пользователя <span class="font-mono font-medium text-fg">{disabling.login}</span> будут закрыты, а
				его аккаунты остановлены.
			</p>
			<label class="block space-y-1">
				<span class="label">Причина отключения (необязательно)</span>
				<input
					class="input"
					bind:value={disableReason}
					maxlength="256"
					placeholder="Например, нарушение правил"
					data-autofocus
				/>
			</label>
			{#if disableError}
				<p class="text-sm text-bad-fg" role="alert">{disableError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (disabling = null)}>Отмена</button>
			<button
				type="submit"
				form="disable-form"
				class="btn btn-danger"
				disabled={actionBusy}
			>
				Отключить
			</button>
		{/snippet}
	</Modal>
{/if}

{#if deleting}
	<Modal title="Удалить пользователя?" onclose={() => (deleting = null)}>
		<form id="delete-user-form" class="space-y-3" onsubmit={confirmDeleteUser}>
			<p class="text-sm text-fg-muted">
				Пользователь <span class="font-mono font-medium text-fg">{deleting.login}</span> и все его аккаунты будут
				удалены навсегда. Действие необратимо.
			</p>
			<label class="block space-y-1">
				<span class="label">Введите точный логин для подтверждения</span>
				<input
					class="input"
					bind:value={confirmLogin}
					placeholder={deleting.login}
					autocomplete="off"
					data-autofocus
				/>
			</label>
			{#if deleteError}
				<p class="text-sm text-bad-fg" role="alert">{deleteError}</p>
			{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (deleting = null)}>Отмена</button>
			<button
				type="submit"
				form="delete-user-form"
				class="btn btn-danger"
				disabled={actionBusy || !deleting || confirmLogin !== deleting.login}
			>
				Удалить навсегда
			</button>
		{/snippet}
	</Modal>
{/if}
