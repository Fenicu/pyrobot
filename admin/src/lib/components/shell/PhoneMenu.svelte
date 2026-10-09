<script lang="ts">
	import { phoneMenu, type NavItem } from '$lib/nav';
	import { APP_VERSION } from '$lib/stores/whatsnew.svelte';
	import Modal from '../Modal.svelte';
	import ThemeSwitch from '../ThemeSwitch.svelte';
	import Tile from '../ui/Tile.svelte';

	interface Props {
		/** Аккаунт меню; null — только «Общее». */
		account: number | null;
		/** Титул аккаунта — заголовок шторки. */
		title: string | null;
		role?: 'owner' | 'user' | null;
		/** Непрочитанные warn и error открытого аккаунта — у «Уведомлений». */
		unread: number;
		login: string | null;
		onclose: () => void;
		onlogout: () => void;
	}
	let { account, title, role, unread, login, onclose, onlogout }: Props = $props();

	const menu = $derived(phoneMenu(account, role));
	const GRID = 'grid grid-cols-3 gap-2';
</script>

{#snippet tile(item: NavItem)}
	<li class="grid">
		<Tile href={item.href} icon={item.icon} label={item.label} badge={item.badge === 'unread' ? unread : 0} onclick={onclose} />
	</li>
{/snippet}

<Modal title={title ?? 'Меню'} variant="sheet" {onclose}>
	{#if menu.account.length > 0}
		<ul class={GRID} aria-label="Разделы аккаунта">
			{#each menu.account as item (item.href)}{@render tile(item)}{/each}
		</ul>
	{/if}
	<h3 class="card-title mt-4 mb-2">Общее</h3>
	<ul class={GRID} aria-label="Общее">
		{#each menu.common as item (item.href)}{@render tile(item)}{/each}
		<li class="grid"><ThemeSwitch variant="tile" /></li>
	</ul>
	<div class="mt-4 flex items-center gap-2 text-xs text-fg-muted">
		<span class="ext-text truncate">{login}</span>
		<span aria-hidden="true">·</span>
		<a href="/changes" class="font-mono text-fg-faint hover:text-fg-muted" title="История изменений" onclick={onclose}
			>v{APP_VERSION}</a
		>
		<button type="button" class="btn btn-ghost ml-auto min-h-8 px-2" onclick={onlogout}>Выйти</button>
	</div>
</Modal>
