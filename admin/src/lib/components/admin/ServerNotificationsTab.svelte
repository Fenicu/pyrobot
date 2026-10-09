<script lang="ts">
	import CheckCheck from '@lucide/svelte/icons/check-check';
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import Pill from '$lib/components/Pill.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();
	let readBusy = $state(false);

	onMount(() => {
		if (store.notifications.length === 0) {
			void store.loadNotifications();
		}
	});

	const hasUnread = $derived(store.notifications.some((n) => !n.read));
	const maxId = $derived(store.notifications.reduce((max, n) => Math.max(max, n.id), 0));

	async function markAllRead() {
		if (maxId <= 0) return;
		readBusy = true;
		try {
			await store.readNotifications(maxId);
			toasts.show('Все уведомления отмечены прочитанными', 'ok');
		} catch (err: unknown) {
			toasts.show(err instanceof Error ? err.message : String(err), 'error');
		} finally {
			readBusy = false;
		}
	}

	const tone = (l: string) => (l === 'error' ? 'bad' : l === 'warn' ? 'warn' : 'muted');
</script>

<div class="space-y-[14px]">
	<div class="flex items-center justify-between">
		<h2 class="card-title mb-0">Уведомления сервера</h2>
		<button
			type="button"
			class="btn gap-1.5 text-xs"
			disabled={readBusy || !hasUnread}
			onclick={markAllRead}
		>
			<CheckCheck class="size-4" aria-hidden="true" />
			Отметить все прочитанными
		</button>
	</div>

	{#if store.notificationsError}
		<p class="card text-sm text-bad-fg" role="alert">{store.notificationsError}</p>
	{/if}

	{#if store.notificationsLoading && store.notifications.length === 0}
		<p class="text-sm text-fg-muted" role="status">Загрузка уведомлений…</p>
	{:else if store.notifications.length === 0}
		<p class="text-sm text-fg-muted">Уведомлений нет.</p>
	{:else}
		<ul class="card divide-y divide-line-soft p-0" aria-label="Уведомления сервера">
			{#each store.notifications as n (n.id)}
				<li class="flex flex-wrap items-baseline gap-2 px-3 py-2 text-sm {n.read ? 'text-fg-muted' : ''}">
					{#if !n.read}
						<span class="size-2 self-center rounded-full bg-accent" aria-label="не прочитано"></span>
					{/if}
					<Pill tone={tone(n.level)}>{n.level}</Pill>
					<span class="font-mono text-xs font-semibold">{n.code}</span>
					<span class="min-w-0 flex-1 basis-60 text-sm">{n.text}</span>
					<time class="text-xs text-fg-faint" datetime={n.created_at}>{fmtMoment(n.created_at)}</time>
				</li>
			{/each}
		</ul>
	{/if}
</div>
