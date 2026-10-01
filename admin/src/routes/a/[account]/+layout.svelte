<script lang="ts">
	import type { Snippet } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { accounts, current, startAccount } from '$lib/app.svelte';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import { parseAccount, rememberAccount, setScreenAccount } from '$lib/nav';

	let { children }: { children: Snippet } = $props();

	const id = $derived(parseAccount(page.params.account));
	const known = $derived(id !== null && (accounts.list?.some((a) => a.id === id) ?? false));
	// Экран рисуется только с контекстом своего аккаунта: при смене аккаунта в адресе прежний экран
	// уходит сразу, а не после переключения.
	const ctx = $derived(current.ctx !== null && current.ctx.id === id ? current.ctx : null);

	setScreenAccount(() => id);

	// Переключение — до отрисовки экрана: прежний контекст останавливается, у нового — свои поток и
	// хранилища.
	$effect.pre(() => {
		if (known && id !== null && current.ctx?.id !== id) {
			startAccount(id);
			rememberAccount(id);
		}
	});

	// Не число или нет в списке — к списку аккаунтов.
	$effect(() => {
		if (accounts.list !== null && !known) void goto('/accounts', { replaceState: true });
	});
</script>

<!-- Экран создаётся заново для каждого контекста: страничные хранилища (план, итоги, журнал,
     настройки) и подписки на поток — нового аккаунта. -->
{#if ctx}
	{#key ctx}
		{@render children()}
	{/key}
{:else}
	<AccountsPending error={accounts.list === null ? accounts.error : null} onretry={() => void accounts.load()} />
{/if}
