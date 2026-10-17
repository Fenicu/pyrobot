<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { page } from '$app/state';
	import { accounts, api } from '$lib/app.svelte';
	import { errorText } from '$lib/api/errors';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import TotalsByAccount from '$lib/components/overview/TotalsByAccount.svelte';
	import TotalsCard from '$lib/components/overview/TotalsCard.svelte';
	import TotalsChart from '$lib/components/overview/TotalsChart.svelte';
	import { parseDays, PERIODS, windowNote } from '$lib/overview/totals';
	import { TotalsStore } from '$lib/overview/totals.svelte';
	import { accountTitle } from '$lib/util/game';

	const days = $derived(parseDays(page.url.searchParams.get('days')));
	const store = new TotalsStore(api, untrack(() => days));
	const data = $derived(store.data);
	const note = $derived(data ? windowNote(data) : null);

	const nameOf = (id: number) => {
		const a = accounts.list?.find((x) => x.id === id);
		return a ? accountTitle(a) : `#${id}`;
	};

	// Период берётся из адреса: ссылка переключателя, «назад» и закладка работают одинаково.
	$effect(() => {
		const d = days;
		untrack(() => store.setDays(d));
	});

	// Опрос живёт, пока открыт экран.
	onMount(() => {
		store.start();
		return () => store.stop();
	});
</script>

<svelte:head><title>Итоги · pyrobot</title></svelte:head>

<div class="space-y-3.5">
	<nav class="flex gap-1" aria-label="Период">
		{#each PERIODS as p (p.days)}
			<a
				href="?days={p.days}"
				data-sveltekit-replacestate
				data-sveltekit-noscroll
				class="chip {days === p.days ? 'border-accent bg-accent-soft text-accent' : ''}"
				aria-current={days === p.days ? 'page' : undefined}>{p.label}</a
			>
		{/each}
	</nav>
	{#if data === null}
		{#if store.error}
			<p class="card text-sm text-bad-fg" role="alert">Итоги недоступны: {errorText(store.error)}.</p>
		{:else}
			<p class="card text-sm text-fg-muted" role="status">Загрузка итогов…</p>
		{/if}
	{:else}
		{#if store.error}
			<p class="text-xs text-bad-fg" role="status">Не удалось обновить: {errorText(store.error)}. Показаны прежние данные.</p>
		{/if}
		{#if accounts.list === null}
			<AccountsPending error={accounts.error} onretry={() => void accounts.load()} />
		{:else if data.accounts.length === 0}
			<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
				<p>Аккаунтов пока нет.</p>
				<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
			</section>
		{:else}
			{#if note}<p class="text-xs text-warn-fg" role="status">{note}</p>{/if}
			<TotalsCard {data} />
			<section class="space-y-2" aria-label="По аккаунтам">
				<h2 class="card-title">По аккаунтам</h2>
				<TotalsByAccount {data} accounts={accounts.list} />
			</section>
			<TotalsChart {data} {nameOf} />
		{/if}
	{/if}
</div>
