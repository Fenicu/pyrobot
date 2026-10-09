<script lang="ts">
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import { isActive, phoneTabs } from '$lib/nav';

	interface Props {
		/** Аккаунт меню; null — только «Аккаунты» и «Меню». */
		account: number | null;
		/** Подпись вкладки аккаунта. */
		name: string | null;
		path: string;
		/** Аккаунты, требующие внимания, — счётчик у «Аккаунтов». */
		attention: number;
		/** Непрочитанные warn и error открытого аккаунта — точка у «Меню» (там «Уведомления»). */
		unread: number;
		/** Открытый экран — в «Меню». */
		menuActive: boolean;
		menuOpen: boolean;
		onmenu: () => void;
	}
	let { account, name, path, attention, unread, menuActive, menuOpen, onmenu }: Props = $props();

	const tabs = $derived(phoneTabs(account, name));
	const TAB = 'relative flex min-w-0 flex-1 flex-col items-center gap-0.5 py-2 text-[11px]';
	const BADGE =
		'absolute top-1 left-1/2 ml-1.5 min-w-4 rounded-full bg-bad px-1 text-center text-[10px] leading-4 font-semibold text-white tabular-nums';
</script>

<nav
	class="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
	aria-label="Вкладки"
>
	{#each tabs as item (item.href)}
		{@const active = isActive(path, item.href)}
		{@const badge = item.href === '/accounts' ? attention : 0}
		<a
			href={item.href}
			class="{TAB} {active ? 'text-accent' : 'text-fg-muted'}"
			aria-current={active ? 'page' : undefined}
			aria-describedby={badge > 0 ? 'tab-attention' : undefined}
		>
			<item.icon class="size-5" aria-hidden="true" />
			<span class="max-w-full truncate px-1">{item.short ?? item.label}</span>
			{#if badge > 0}
				<span class={BADGE} aria-hidden="true">{badge}</span>
			{/if}
		</a>
	{/each}
	<button
		type="button"
		class="{TAB} {menuActive ? 'text-accent' : 'text-fg-muted'}"
		aria-haspopup="dialog"
		aria-expanded={menuOpen}
		aria-describedby={unread > 0 ? 'tab-unread' : undefined}
		onclick={onmenu}
	>
		<LayoutGrid class="size-5" aria-hidden="true" />
		Меню
		{#if unread > 0}
			<span class="absolute top-1.5 left-1/2 ml-2 size-2 rounded-full bg-bad" aria-hidden="true"></span>
		{/if}
	</button>
	<!-- Описания счётчиков — вне ссылок, чтобы не входить в их имена. -->
	{#if attention > 0}<span id="tab-attention" class="sr-only">требуют внимания: {attention}</span>{/if}
	{#if unread > 0}<span id="tab-unread" class="sr-only">непрочитанных предупреждений и ошибок: {unread}</span>{/if}
</nav>
