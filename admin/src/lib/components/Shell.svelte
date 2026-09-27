<script lang="ts">
	import Ellipsis from '@lucide/svelte/icons/ellipsis';
	import LogOut from '@lucide/svelte/icons/log-out';
	import type { Snippet } from 'svelte';
	import { page } from '$app/state';
	import { live, session, unread } from '$lib/app.svelte';
	import { signOutWithRetry } from '$lib/logout';
	import { isActive, MAIN_NAV, MORE_NAV, type NavItem } from '$lib/nav';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import ConnectionDot from './ConnectionDot.svelte';
	import Modal from './Modal.svelte';
	import ThemeSwitch from './ThemeSwitch.svelte';

	let { children }: { children: Snippet } = $props();
	let moreOpen = $state(false);
	const path = $derived(page.url.pathname);
	const moreActive = $derived(MORE_NAV.some((i) => isActive(path, i.href)));

	function badge(item: NavItem): number {
		return item.badge === 'unread' ? unread.count : 0;
	}

	async function logout() {
		moreOpen = false;
		if (await dialogs.confirm({ title: 'Выйти из админки?', confirmText: 'Выйти' })) {
			await signOutWithRetry(session, dialogs);
		}
	}
</script>

{#snippet link(item: NavItem, onclick?: () => void)}
	<a
		href={item.href}
		{onclick}
		class="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-surface-2 {isActive(
			path,
			item.href
		)
			? 'bg-accent-soft font-medium text-fg'
			: 'text-fg-muted'}"
		aria-current={isActive(path, item.href) ? 'page' : undefined}
	>
		<item.icon class="size-4 shrink-0" aria-hidden="true" />
		<span class="flex-1">{item.label}</span>
		{#if badge(item) > 0}
			<span class="pill pill-bad" aria-label="непрочитанных: {badge(item)}">{badge(item)}</span>
		{/if}
	</a>
{/snippet}

<div class="min-h-dvh md:flex">
	<aside
		class="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r border-line bg-surface md:flex"
		aria-label="Меню"
	>
		<div class="flex items-center justify-between px-4 py-3">
			<span class="text-base font-semibold">pyrobot</span>
			<ConnectionDot status={live.status} retryIn={live.retryIn} compact />
		</div>
		<nav class="flex flex-1 flex-col gap-0.5 px-2" aria-label="Разделы">
			{#each MAIN_NAV as item (item.href)}{@render link(item)}{/each}
			<div class="my-2 border-t border-line-soft"></div>
			{#each MORE_NAV as item (item.href)}{@render link(item)}{/each}
		</nav>
		<div class="space-y-2 border-t border-line-soft px-3 py-3">
			<ThemeSwitch />
			<div class="flex items-center justify-between text-xs text-fg-muted">
				<span>{session.login}</span>
				<button type="button" class="btn btn-ghost min-h-8 px-2" onclick={logout}>
					<LogOut class="size-4" aria-hidden="true" /> Выйти
				</button>
			</div>
		</div>
	</aside>

	<main class="min-w-0 flex-1 px-3 pt-3 pb-24 md:px-6 md:pt-5 md:pb-8">
		{@render children()}
	</main>

	<nav
		class="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
		aria-label="Разделы"
	>
		{#each MAIN_NAV as item (item.href)}
			<a
				href={item.href}
				class="flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] {isActive(path, item.href)
					? 'text-accent'
					: 'text-fg-muted'}"
				aria-current={isActive(path, item.href) ? 'page' : undefined}
			>
				<item.icon class="size-5" aria-hidden="true" />
				{item.short ?? item.label}
			</a>
		{/each}
		<button
			type="button"
			class="relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] {moreActive
				? 'text-accent'
				: 'text-fg-muted'}"
			aria-haspopup="dialog"
			aria-expanded={moreOpen}
			onclick={() => (moreOpen = true)}
		>
			<Ellipsis class="size-5" aria-hidden="true" />
			Ещё
			{#if unread.count > 0}
				<span class="absolute top-1 right-1/4 size-2 rounded-full bg-red-500" aria-hidden="true"></span>
			{/if}
		</button>
	</nav>
</div>

{#if moreOpen}
	<Modal title="Ещё" variant="sheet" onclose={() => (moreOpen = false)}>
		<div class="mb-3 flex items-center justify-between">
			<ConnectionDot status={live.status} retryIn={live.retryIn} />
			<ThemeSwitch />
		</div>
		<nav class="flex flex-col gap-0.5" aria-label="Ещё">
			{#each MORE_NAV as item (item.href)}{@render link(item, () => (moreOpen = false))}{/each}
			<button
				type="button"
				class="flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm text-fg-muted hover:bg-surface-2"
				onclick={logout}
			>
				<LogOut class="size-4" aria-hidden="true" /> Выйти ({session.login})
			</button>
		</nav>
	</Modal>
{/if}
