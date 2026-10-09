<script lang="ts">
	import Ellipsis from '@lucide/svelte/icons/ellipsis';
	import LogOut from '@lucide/svelte/icons/log-out';
	import type { Snippet } from 'svelte';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { accounts, api, current, session } from '$lib/app.svelte';
	import { signOutWithRetry } from '$lib/logout';
	import { isActive, lastAccount, mainNav, moreNav, parseAccount, pickAccount, type NavItem } from '$lib/nav';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { APP_VERSION, whatsNew } from '$lib/stores/whatsnew.svelte';
	import AccountSwitcher from './AccountSwitcher.svelte';
	import WhatsNewDialog from './changes/WhatsNewDialog.svelte';
	import ConnectionDot from './ConnectionDot.svelte';
	import Modal from './Modal.svelte';
	import AccountsColumn from './shell/AccountsColumn.svelte';
	import Rail from './shell/Rail.svelte';
	import ThemeSwitch from './ThemeSwitch.svelte';

	let { children }: { children: Snippet } = $props();
	let moreOpen = $state(false);
	const path = $derived(page.url.pathname);
	// Аккаунт меню: из адреса, на общих экранах — открытый, иначе тот, куда ведёт «/». Аккаунт из
	// адреса, которого нет в загруженном списке (удалён), меню не открывает.
	const ctx = $derived(current.ctx);
	const fromUrl = $derived(parseAccount(page.params.account));
	const account = $derived(
		(fromUrl !== null && (accounts.list?.some((a) => a.id === fromUrl) ?? true) ? fromUrl : null) ??
			ctx?.id ??
			pickAccount(accounts.list ?? [], lastAccount())
	);
	// Его контекст — если открыт: точка связи и счётчик непрочитанных из потока.
	const opened = $derived(ctx?.id === account ? ctx : null);
	const stopped = $derived(opened?.engine.status?.running === false);
	const main = $derived(account === null ? [] : mainNav(account));
	const more = $derived(moreNav(account, session.role));
	const moreActive = $derived(more.some((i) => isActive(path, i.href)));
	const unread = $derived(opened?.unread.count ?? 0);

	onMount(() => whatsNew.check());

	function badge(item: NavItem): number {
		return item.badge === 'unread' ? unread : 0;
	}

	async function logout() {
		moreOpen = false;
		if (await dialogs.confirm({ title: 'Выйти из админки?', confirmText: 'Выйти' })) {
			await signOutWithRetry(session, dialogs);
		}
	}
</script>

{#snippet version(onclick?: () => void)}
	<a href="/changes" {onclick} class="block font-mono text-xs text-fg-faint hover:text-fg-muted" title="История изменений"
		>v{APP_VERSION}</a
	>
{/snippet}

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
			<span class="pill pill-bad" aria-label="непрочитанных предупреждений и ошибок: {badge(item)}">{badge(item)}</span>
		{/if}
	</a>
{/snippet}

<div class="min-h-dvh md:flex">
	<div class="hidden md:contents">
		<Rail {account} role={session.role} {path} {unread} login={session.login} onlogout={logout} />
		<AccountsColumn {api} store={accounts} current={account} {path} />
	</div>

	<main class="min-w-0 flex-1 pb-[calc(6rem_+_env(safe-area-inset-bottom))] md:pb-0">
		<div class="px-3.5 pt-3 md:hidden">
			<AccountSwitcher
				variant="bar"
				accounts={accounts.list}
				current={account}
				alerts={opened?.unread.count}
				{path}
				onopen={() => (moreOpen = true)}
			/>
		</div>
		{@render children()}
	</main>

	<nav
		class="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden"
		aria-label="Разделы"
	>
		{#each main as item (item.href)}
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
			{#if unread > 0}
				<span class="absolute top-1 right-1/4 size-2 rounded-full bg-red-500" aria-hidden="true"></span>
			{/if}
		</button>
	</nav>
</div>

{#if moreOpen}
	<Modal title="Ещё" variant="sheet" onclose={() => (moreOpen = false)}>
		<div class="mb-3 flex items-center justify-between">
			<ConnectionDot status={opened?.live.status ?? 'idle'} retryIn={opened?.live.retryIn ?? 0} {stopped} />
			<ThemeSwitch />
		</div>
		<AccountSwitcher
			variant="list"
			accounts={accounts.list}
			current={account}
			alerts={opened?.unread.count}
			{path}
			onpick={() => (moreOpen = false)}
		/>
		<nav class="flex flex-col gap-0.5" aria-label="Ещё">
			{#each more as item (item.href)}{@render link(item, () => (moreOpen = false))}{/each}
			<button
				type="button"
				class="flex items-center gap-3 rounded-md px-3 py-2 text-left text-sm text-fg-muted hover:bg-surface-2"
				onclick={logout}
			>
				<LogOut class="size-4" aria-hidden="true" /> Выйти ({session.login})
			</button>
		</nav>
		<div class="mt-3 px-3">{@render version(() => (moreOpen = false))}</div>
	</Modal>
{/if}

{#if whatsNew.open}
	<WhatsNewDialog entries={whatsNew.entries} onclose={() => whatsNew.dismiss()} />
{/if}
