<script lang="ts">
	import { onMount } from 'svelte';
	import { accounts, api } from '$lib/app.svelte';
	import { errorText } from '$lib/api/errors';
	import FeedFilters from '$lib/components/overview/FeedFilters.svelte';
	import FeedList from '$lib/components/overview/FeedList.svelte';
	import { FeedStore } from '$lib/overview/feed.svelte';

	const store = new FeedStore(api);
	// «16.10 22:00» или «11:58» зависит от текущих суток: часы страницы.
	let now = $state(new Date());
	const empty = $derived(accounts.list !== null && accounts.list.length === 0);

	// Опрос живёт, пока открыт экран.
	onMount(() => {
		if (!empty) store.start();
		const t = setInterval(() => (now = new Date()), 30_000);
		return () => {
			clearInterval(t);
			store.stop();
		};
	});

	// Аккаунты пришли позже экрана и оказались пустыми - ленту не опрашиваем.
	$effect(() => {
		if (empty) store.stop();
	});
</script>

<svelte:head><title>Лента · pyrobot</title></svelte:head>

{#if empty}
	<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
		<p>Аккаунтов пока нет.</p>
		<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
	</section>
{:else}
	<div class="space-y-3">
		<FeedFilters accounts={accounts.list} value={store.filters} onchange={(f) => store.setFilters(f)} />
		{#if !store.loaded && store.error === null}
			<p class="card text-sm text-fg-muted" role="status">Загрузка…</p>
		{:else if !store.loaded}
			<p class="card text-sm text-bad-fg" role="alert">Лента недоступна: {errorText(store.error!)}.</p>
		{:else}
			{#if store.error}
				<p class="text-xs text-bad-fg" role="status">Не удалось обновить: {errorText(store.error)}. Показаны прежние данные.</p>
			{/if}
			<FeedList items={store.items} accounts={accounts.list} {now} />
			{#if store.hasOlder}
				<button type="button" class="btn w-full md:w-auto" disabled={store.loadingMore} onclick={() => void store.loadMore()}>Загрузить ещё</button>
			{/if}
		{/if}
	</div>
{/if}
