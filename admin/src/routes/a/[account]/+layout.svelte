<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { accounts, api, current, startAccount } from '$lib/app.svelte';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import EngineDownBanner from '$lib/components/EngineDownBanner.svelte';
	import GameChatBanner from '$lib/components/GameChatBanner.svelte';
	import { setAccountFrame } from '$lib/components/shell/frame';
	import { parseAccount, rememberAccount, setScreenAccount } from '$lib/nav';
	import { accountTitle } from '$lib/util/game';

	let { children }: { children: Snippet } = $props();

	const id = $derived(parseAccount(page.params.account));
	const known = $derived(id !== null && (accounts.list?.some((a) => a.id === id) ?? false));
	// Экран рисуется только с контекстом своего аккаунта: при смене аккаунта в адресе прежний экран
	// уходит сразу, а не после переключения.
	const ctx = $derived(current.ctx !== null && current.ctx.id === id ? current.ctx : null);

	setScreenAccount(() => id);
	// Шапка страницы: имя аккаунта, связь его потока; плашки — под шапкой.
	setAccountFrame(() => {
		if (ctx === null) return null;
		const acc = accounts.list?.find((a) => a.id === ctx.id);
		return {
			title: acc ? accountTitle(acc) : `#${ctx.id}`,
			live: ctx.live.status,
			retryIn: ctx.live.retryIn,
			stopped: ctx.engine.status?.running === false,
			banners
		};
	});

	// Переключение — до отрисовки экрана: прежний контекст останавливается, у нового — свои поток и
	// хранилища. Зависимости — только адрес, список и открытый аккаунт: чтения внутри запуска
	// (контекст прежнего аккаунта) эффект не отслеживает.
	$effect.pre(() => {
		if (known && id !== null && current.ctx?.id !== id) {
			untrack(() => {
				startAccount(id);
				rememberAccount(id);
			});
		}
	});

	// Включение из плашки «движок не запущен»: статус движка и список аккаунтов — заново.
	function reload() {
		void current.ctx?.engine.load();
		void accounts.load();
	}

	// Не число или нет в списке — к списку аккаунтов.
	$effect(() => {
		if (accounts.list !== null && !known) void goto('/accounts', { replaceState: true });
	});
</script>

{#snippet banners()}
	{#if ctx && ctx.engine.status}
		{@const c = ctx}
		{@const status = ctx.engine.status}
		{@const acc = accounts.list?.find((a) => a.id === c.id) ?? null}
		<EngineDownBanner
			{status}
			accountId={c.id}
			{api}
			onchange={reload}
			blocked={acc?.blocked ?? false}
			blockedReason={acc?.blocked_reason ?? null}
		/>
		<GameChatBanner {status} api={c.api} onchange={() => void c.engine.load()} />
	{/if}
{/snippet}

<!-- Экран создаётся заново для каждого контекста: страничные хранилища (план, итоги, журнал,
     настройки) и подписки на поток — нового аккаунта. -->
{#if ctx}
	{#key ctx}
		{@render children()}
	{/key}
{:else}
	<div class="p-3.5">
		<AccountsPending error={accounts.list === null ? accounts.error : null} onretry={() => void accounts.load()} />
	</div>
{/if}
