<script lang="ts">
	import Bell from '@lucide/svelte/icons/bell';
	import History from '@lucide/svelte/icons/history';
	import Mail from '@lucide/svelte/icons/mail';
	import Server from '@lucide/svelte/icons/server';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import Users from '@lucide/svelte/icons/users';
	import UsersRound from '@lucide/svelte/icons/users-round';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import UsersTab from './UsersTab.svelte';
	import AccountsTab from './AccountsTab.svelte';
	import InvitesTab from './InvitesTab.svelte';
	import ServerTab from './ServerTab.svelte';
	import AuditTab from './AuditTab.svelte';
	import ServerNotificationsTab from './ServerNotificationsTab.svelte';

	export type AdminTab = 'users' | 'accounts' | 'invites' | 'server' | 'audit' | 'notifications';

	interface Props {
		store: AdminStore;
		role?: 'owner' | 'user' | null;
		initialTab?: AdminTab;
	}

	let { store, role = 'owner', initialTab }: Props = $props();

	const validTabs = new Set<AdminTab>(['users', 'accounts', 'invites', 'server', 'audit', 'notifications']);

	function getTabFromUrl(): AdminTab {
		if (initialTab && validTabs.has(initialTab)) return initialTab;
		const queryTab = page.url.searchParams.get('tab') as AdminTab | null;
		if (queryTab && validTabs.has(queryTab)) return queryTab;
		return 'users';
	}

	let currentTab = $state<AdminTab>(getTabFromUrl());

	$effect(() => {
		const fromUrl = page.url.searchParams.get('tab') as AdminTab | null;
		if (fromUrl && validTabs.has(fromUrl)) {
			currentTab = fromUrl;
		}
	});

	function switchTab(tab: AdminTab) {
		currentTab = tab;
		const url = new URL(page.url);
		url.searchParams.set('tab', tab);
		void goto(url.pathname + url.search, { replaceState: true, noScroll: true });
	}

	const tabs: { id: AdminTab; label: string; icon: typeof Users }[] = [
		{ id: 'users', label: 'Пользователи', icon: Users },
		{ id: 'accounts', label: 'Аккаунты', icon: UsersRound },
		{ id: 'invites', label: 'Приглашения', icon: Mail },
		{ id: 'server', label: 'Сервер', icon: Server },
		{ id: 'audit', label: 'Журнал действий', icon: History },
		{ id: 'notifications', label: 'Уведомления', icon: Bell }
	];

	const isNotOwner = $derived(role !== 'owner' || store.forbidden);
</script>

{#if isNotOwner}
	<div class="card my-6 flex flex-col items-center justify-center p-8 text-center" role="alert">
		<ShieldAlert class="size-12 text-bad-fg" aria-hidden="true" />
		<h2 class="mt-4 text-lg font-semibold text-fg">Раздел только для владельца сервера</h2>
		<p class="mt-2 text-sm text-fg-muted">
			У вашей учётной записи нет прав для просмотра панели управления сервером.
		</p>
		<div class="mt-6">
			<a href="/" class="btn btn-primary">Перейти на главную</a>
		</div>
	</div>
{:else}
	<div class="space-y-4">
		<nav class="flex gap-1 overflow-x-auto border-b border-line-soft pb-2" aria-label="Вкладки сервера">
			{#each tabs as t (t.id)}
				<button
					type="button"
					class="flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors {currentTab ===
					t.id
						? 'bg-accent-soft text-accent'
						: 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
					aria-current={currentTab === t.id ? 'page' : undefined}
					onclick={() => switchTab(t.id)}
				>
					<t.icon class="size-4 shrink-0" aria-hidden="true" />
					<span>{t.label}</span>
				</button>
			{/each}
		</nav>

		<main>
			{#if currentTab === 'users'}
				<UsersTab {store} />
			{:else if currentTab === 'accounts'}
				<AccountsTab {store} />
			{:else if currentTab === 'invites'}
				<InvitesTab {store} />
			{:else if currentTab === 'server'}
				<ServerTab {store} />
			{:else if currentTab === 'audit'}
				<AuditTab {store} />
			{:else if currentTab === 'notifications'}
				<ServerNotificationsTab {store} />
			{/if}
		</main>
	</div>
{/if}
