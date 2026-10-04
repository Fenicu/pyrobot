<script lang="ts">
	import { untrack, type Snippet } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { accounts, api, current, startAccount } from '$lib/app.svelte';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import EngineDownBanner from '$lib/components/EngineDownBanner.svelte';
	import GameChatBanner from '$lib/components/GameChatBanner.svelte';
	import { parseAccount, rememberAccount, setScreenAccount } from '$lib/nav';

	let { children }: { children: Snippet } = $props();

	const id = $derived(parseAccount(page.params.account));
	const known = $derived(id !== null && (accounts.list?.some((a) => a.id === id) ?? false));
	// Экран рисуется только с контекстом своего аккаунта: при смене аккаунта в адресе прежний экран
	// уходит сразу, а не после переключения.
	const ctx = $derived(current.ctx !== null && current.ctx.id === id ? current.ctx : null);

	setScreenAccount(() => id);

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

<!-- Экран создаётся заново для каждого контекста: страничные хранилища (план, итоги, журнал,
     настройки) и подписки на поток — нового аккаунта. -->
{#if ctx}
	{#key ctx}
		{#if ctx.engine.status}
			{@const acc = accounts.list?.find((a) => a.id === ctx.id) ?? null}
			<EngineDownBanner
				status={ctx.engine.status}
				accountId={ctx.id}
				{api}
				onchange={reload}
				blocked={acc?.blocked ?? false}
				blockedReason={acc?.blocked_reason ?? null}
			/>
			<GameChatBanner status={ctx.engine.status} api={ctx.api} onchange={() => void ctx.engine.load()} />
		{/if}
		{@render children()}
	{/key}
{:else}
	<AccountsPending error={accounts.list === null ? accounts.error : null} onretry={() => void accounts.load()} />
{/if}
