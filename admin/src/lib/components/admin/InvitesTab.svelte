<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import type { AdminInviteCreatedOut, AdminInviteOut } from '$lib/api/types';
	import Pill from '$lib/components/Pill.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();

	let maxAccounts = $state<string | number>('');
	let ttlH = $state<string | number>('');
	let note = $state<string>('');
	let createError = $state('');
	let actionBusy = $state(false);

	let createdResult = $state<AdminInviteCreatedOut | null>(null);
	let copied = $state(false);

	onMount(() => {
		if (!store.invites) {
			void store.loadInvites();
		}
	});

	const origin = typeof location !== 'undefined' ? location.origin : '';
	const inviteUrl = $derived(createdResult ? `${origin}${createdResult.path}` : '');

	async function createInvite(e: SubmitEvent) {
		e.preventDefault();
		createError = '';
		actionBusy = true;

		const rawMax = String(maxAccounts ?? '').trim();
		const rawTtl = String(ttlH ?? '').trim();
		const rawNote = String(note ?? '').trim();

		const numMax = rawMax !== '' && !Number.isNaN(Number(rawMax)) ? Number(rawMax) : null;
		const numTtl = rawTtl !== '' && !Number.isNaN(Number(rawTtl)) ? Number(rawTtl) : null;
		const strNote = rawNote !== '' ? rawNote : null;

		if (numMax !== null && (!Number.isInteger(numMax) || numMax < 1 || numMax > 1000)) {
			createError = 'Лимит аккаунтов должен быть целым числом от 1 до 1000';
			actionBusy = false;
			return;
		}

		if (numTtl !== null && (!Number.isInteger(numTtl) || numTtl < 1 || numTtl > 720)) {
			createError = 'Срок приглашения должен быть целым числом от 1 до 720 часов';
			actionBusy = false;
			return;
		}

		try {
			const res = await store.createInvite({
				max_accounts: numMax,
				ttl_h: numTtl,
				note: strNote
			});
			createdResult = res;
			copied = false;
			maxAccounts = '';
			ttlH = '';
			note = '';
		} catch (err: unknown) {
			createError = err instanceof Error ? err.message : String(err);
		} finally {
			actionBusy = false;
		}
	}

	async function copyLink() {
		if (!inviteUrl) return;
		try {
			await navigator.clipboard.writeText(inviteUrl);
			copied = true;
			toasts.show('Ссылка приглашения скопирована', 'ok');
		} catch {
			toasts.show('Не удалось скопировать ссылку', 'error');
		}
	}

	async function revoke(invite: AdminInviteOut) {
		actionBusy = true;
		try {
			await store.revokeInvite(invite.id);
			toasts.show('Приглашение отозвано', 'ok');
		} catch (err: unknown) {
			toasts.show(err instanceof Error ? err.message : String(err), 'error');
			await store.loadInvites();
		} finally {
			actionBusy = false;
		}
	}
</script>

<div class="space-y-[14px]">
	{#if store.invitesError}
		<p class="card text-sm text-bad-fg" role="alert">{store.invitesError}</p>
	{/if}

	{#if createdResult}
		<div class="card space-y-3 border-accent bg-accent-soft p-4" role="region" aria-label="Созданное приглашение">
			<h2 class="card-title mb-0">Приглашение создано</h2>
			<p class="text-xs text-fg-muted">
				Ссылка с секретным токеном показывается только один раз. Скопируйте её и передайте пользователю.
			</p>

			<div class="flex flex-col gap-2 sm:flex-row sm:items-center">
				<span class="sr-only">{inviteUrl}</span>
				<input
					readonly
					class="input font-mono text-xs flex-1 bg-surface"
					value={inviteUrl}
					aria-label="Ссылка приглашения"
				/>
				<button type="button" class="btn btn-primary gap-1.5" onclick={copyLink}>
					{#if copied}
						<Check class="size-4" aria-hidden="true" />
						Скопировано
					{:else}
						<Copy class="size-4" aria-hidden="true" />
						Скопировать
					{/if}
				</button>
				<button type="button" class="btn" onclick={() => (createdResult = null)}>
					Закрыть
				</button>
			</div>
		</div>
	{/if}

	<form class="card space-y-3" onsubmit={createInvite} novalidate>
		<h2 class="card-title mb-0">Новое приглашение</h2>
		<div class="grid gap-3 sm:grid-cols-3">
			<label class="block space-y-1">
				<span class="label">Лимит аккаунтов (1..1000)</span>
				<input
					type="number"
					class="input"
					bind:value={maxAccounts}
					min="1"
					max="1000"
					placeholder="По умолчанию (1)"
				/>
			</label>

			<label class="block space-y-1">
				<span class="label">Срок в часах (1..720)</span>
				<input
					type="number"
					class="input"
					bind:value={ttlH}
					min="1"
					max="720"
					placeholder="По умолчанию (72)"
				/>
			</label>

			<label class="block space-y-1">
				<span class="label">Пометка (кому)</span>
				<input
					type="text"
					class="input"
					bind:value={note}
					maxlength="128"
					placeholder="Например, коллега"
				/>
			</label>
		</div>

		{#if createError}
			<p class="text-sm text-bad-fg" role="alert">{createError}</p>
		{/if}

		<div>
			<button type="submit" class="btn btn-primary" disabled={actionBusy}>
				Создать приглашение
			</button>
		</div>
	</form>

	<section class="space-y-2">
		<h2 class="card-title mb-0">Неиспользованные приглашения</h2>

		{#if store.invitesLoading && !store.invites}
			<p class="text-sm text-fg-muted" role="status">Загрузка приглашений…</p>
		{:else if store.invites && store.invites.length === 0}
			<p class="text-sm text-fg-muted">Неиспользованных приглашений нет.</p>
		{:else if store.invites}
			<div
				role="table"
				aria-label="Приглашения"
				class="space-y-2 md:space-y-0 md:overflow-hidden md:rounded-lg md:border md:border-line md:bg-surface"
			>
				<div
					role="row"
					class="hidden gap-3 border-b border-line-soft px-3 py-2 text-xs font-semibold tracking-wide text-fg-muted uppercase md:grid md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1.5fr)_6rem]"
				>
					<span role="columnheader">Создано</span>
					<span role="columnheader">Истекает</span>
					<span role="columnheader">Лимит</span>
					<span role="columnheader">Статус</span>
					<span role="columnheader">Пометка</span>
					<span role="columnheader" class="sr-only">Действия</span>
				</div>

				{#each store.invites as inv (inv.id)}
					<div
						role="row"
						class="card grid gap-2 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.2fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1.5fr)_6rem] md:items-center md:gap-3 md:rounded-none md:border-0 md:border-b md:border-line-soft md:last:border-b-0"
					>
						<div role="cell" class="text-xs text-fg-muted">
							<span class="md:hidden">Создано: </span>
							{fmtMoment(inv.created_at)}
						</div>

						<div role="cell" class="text-xs text-fg-muted">
							<span class="md:hidden">Истекает: </span>
							{fmtMoment(inv.expires_at)}
						</div>

						<div role="cell" class="text-sm">
							<span class="text-xs text-fg-muted md:hidden">Лимит: </span>
							{inv.max_accounts}
						</div>

						<div role="cell">
							{#if inv.expired}
								<Pill tone="bad">истекло</Pill>
							{:else}
								<Pill tone="ok">активно</Pill>
							{/if}
						</div>

						<div role="cell" class="min-w-0 text-sm">
							<span class="text-xs text-fg-muted md:hidden">Пометка: </span>
							<span>{inv.note ?? '—'}</span>
						</div>

						<div role="cell" class="flex items-center md:justify-end">
							<button
								type="button"
								class="btn btn-ghost px-2 py-1 text-xs text-bad-fg"
								title="Отозвать приглашение"
								onclick={() => revoke(inv)}
								disabled={actionBusy}
							>
								<Trash2 class="size-3.5" aria-hidden="true" />
								<span class="md:sr-only">Отозвать</span>
							</button>
						</div>
					</div>
				{/each}
			</div>
		{/if}
	</section>
</div>
