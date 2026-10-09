<script lang="ts">
	import type { Snippet } from 'svelte';
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { TONE_LABEL, accountTone, needsAttention } from '$lib/accounts/status';
	import { accounts, api, current, session } from '$lib/app.svelte';
	import { signOutWithRetry } from '$lib/logout';
	import { accountHref, isActive, lastAccount, parseAccount, phoneMenu, pickAccount } from '$lib/nav';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { whatsNew } from '$lib/stores/whatsnew.svelte';
	import { accountTitle } from '$lib/util/game';
	import WhatsNewDialog from './changes/WhatsNewDialog.svelte';
	import Pill from './Pill.svelte';
	import AccountsColumn from './shell/AccountsColumn.svelte';
	import { setPageTitleSink } from './shell/frame';
	import PhoneMenu from './shell/PhoneMenu.svelte';
	import PhoneTabs from './shell/PhoneTabs.svelte';
	import Rail from './shell/Rail.svelte';
	import StatusDot from './ui/StatusDot.svelte';

	let { children }: { children: Snippet } = $props();
	let menuOpen = $state(false);
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
	const acc = $derived(accounts.list?.find((a) => a.id === account) ?? null);
	// Его контекст — если открыт: счётчик непрочитанных и режим из потока, свежее списка.
	const opened = $derived(ctx?.id === account ? ctx : null);
	const unread = $derived(opened?.unread.count ?? (acc ? acc.unread.warn + acc.unread.error : 0));
	const attention = $derived((accounts.list ?? []).filter(needsAttention).length);
	const menuActive = $derived(
		path === '/changes' ||
			Object.values(phoneMenu(account, session.role)).some((items) => items.some((i) => isActive(path, i.href)))
	);

	// Раздел для верхней полосы телефона — от страницы (Page); со старого адреса не берётся.
	let section = $state<{ path: string; title: string } | null>(null);
	setPageTitleSink((title) => (section = { path: untrack(() => page.url.pathname), title }));
	const sectionTitle = $derived(section?.path === path ? section.title : null);
	const onAccountScreen = $derived(acc !== null && fromUrl === acc.id);
	const mode = $derived(opened?.engine.status?.mode ?? acc?.mode ?? null);

	onMount(() => whatsNew.check());

	async function logout() {
		menuOpen = false;
		if (await dialogs.confirm({ title: 'Выйти из админки?', confirmText: 'Выйти' })) {
			await signOutWithRetry(session, dialogs);
		}
	}
</script>

<div class="min-h-dvh md:flex">
	<div class="hidden md:contents">
		<Rail {account} role={session.role} {path} {unread} login={session.login} onlogout={logout} />
		<AccountsColumn {api} store={accounts} current={account} {path} />
	</div>

	<header
		aria-label="Открытый экран"
		class="sticky top-0 z-30 flex h-[42px] items-center gap-2 border-b border-line bg-surface px-3.5 md:hidden"
	>
		{#if onAccountScreen && acc}
			{@const tone = accountTone(acc)}
			<StatusDot {tone} label={TONE_LABEL[tone]} />
			<span class="min-w-0 flex-1 truncate text-[15px] font-semibold"
				>{accountTitle(acc)}{#if sectionTitle}{' '}<span class="font-normal text-fg-muted">· {sectionTitle}</span
					>{/if}</span
			>
			{#if mode === 'live'}
				<Pill tone="ok">LIVE</Pill>
			{:else if mode === 'dry_run'}
				<Pill tone="warn" title="Команды, кроме навигации, не уходят в игру">DRY RUN</Pill>
			{/if}
			{#if unread > 0}
				<a
					href={accountHref(acc.id, '/notifications')}
					class="pill pill-bad tabular-nums"
					aria-label="непрочитанных предупреждений и ошибок: {unread}">{unread}</a
				>
			{/if}
		{:else}
			<span class="min-w-0 flex-1 truncate text-[15px] font-semibold">{sectionTitle ?? ''}</span>
		{/if}
	</header>

	<main class="min-w-0 flex-1 pb-[calc(6rem_+_env(safe-area-inset-bottom))] md:pb-0">
		{@render children()}
	</main>

	<PhoneTabs
		{account}
		name={acc?.name ?? null}
		{path}
		{attention}
		{unread}
		{menuActive}
		{menuOpen}
		onmenu={() => (menuOpen = true)}
	/>
</div>

{#if menuOpen}
	<PhoneMenu
		{account}
		title={acc ? accountTitle(acc) : null}
		role={session.role}
		{unread}
		login={session.login}
		onclose={() => (menuOpen = false)}
		onlogout={logout}
	/>
{/if}

{#if whatsNew.open}
	<WhatsNewDialog entries={whatsNew.entries} onclose={() => whatsNew.dismiss()} />
{/if}
