<script lang="ts">
	import LogOut from '@lucide/svelte/icons/log-out';
	import { isActive, railNav, type NavItem } from '$lib/nav';
	import { APP_VERSION } from '$lib/stores/whatsnew.svelte';
	import ThemeSwitch from '../ThemeSwitch.svelte';

	interface Props {
		/** Аккаунт меню; null — разделов аккаунта нет. */
		account: number | null;
		role?: 'owner' | 'user' | null;
		path: string;
		/** Непрочитанные warn и error открытого аккаунта. */
		unread: number;
		login: string | null;
		onlogout: () => void;
	}
	let { account, role, path, unread, login, onlogout }: Props = $props();

	const nav = $derived(railNav(account, role));
	const ICON = 'relative flex size-[34px] shrink-0 items-center justify-center rounded-[10px]';
</script>

{#snippet link(item: NavItem)}
	{@const active = isActive(path, item.href)}
	{@const badge = item.badge === 'unread' ? unread : 0}
	<a
		href={item.href}
		class="{ICON} {active ? 'bg-accent-soft text-accent' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
		title={item.label}
		aria-label={item.label}
		aria-current={active ? 'page' : undefined}
		aria-describedby={badge > 0 ? 'rail-unread' : undefined}
	>
		<item.icon class="size-[18px]" aria-hidden="true" />
		{#if badge > 0}
			<span
				class="absolute -top-0.5 -right-0.5 min-w-4 rounded-full bg-bad px-1 text-center text-[10px] leading-4 font-semibold text-white"
				aria-hidden="true">{badge > 99 ? '99+' : badge}</span
			>
			<span id="rail-unread" class="sr-only">непрочитанных предупреждений и ошибок: {badge}</span>
		{/if}
	</a>
{/snippet}

<nav
	class="sticky top-0 flex h-dvh w-[52px] shrink-0 flex-col overflow-y-auto items-center gap-1.5 border-r border-line bg-surface px-1.5 py-2.5"
	aria-label="Полоса разделов"
>
	<span class="{ICON} font-bold text-fg" title="pyrobot" aria-hidden="true">p</span>
	{#each nav.account as item (item.href)}{@render link(item)}{/each}
	<span class="flex-1"></span>
	{#each nav.common as item (item.href)}{@render link(item)}{/each}
	<ThemeSwitch compact />
	<button
		type="button"
		class="{ICON} text-fg-muted hover:bg-surface-2 hover:text-fg"
		aria-label="Выйти ({login})"
		title="Выйти ({login})"
		onclick={onlogout}
	>
		<LogOut class="size-[18px]" aria-hidden="true" />
	</button>
	<a
		href="/changes"
		class="w-full text-center font-mono text-[9px] leading-tight break-all text-fg-faint hover:text-fg-muted"
		title="История изменений">v{APP_VERSION}</a
	>
</nav>
