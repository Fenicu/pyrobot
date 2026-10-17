<script lang="ts">
	import { onMount } from 'svelte';
	import { accounts, api } from '$lib/app.svelte';
	import { errorText } from '$lib/api/errors';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import SummaryCards from '$lib/components/overview/SummaryCards.svelte';
	import SummaryTable from '$lib/components/overview/SummaryTable.svelte';
	import { OverviewAccountsStore } from '$lib/overview/accounts.svelte';
	import { loadSort, mergeRows, nextSort, saveSort, sortRows, type SortKey } from '$lib/overview/table';

	const store = new OverviewAccountsStore(api);
	let sort = $state(loadSort());
	// «до 14:40» и «снимок от …» кончаются между опросами: часы страницы.
	let now = $state(new Date());

	const list = $derived(accounts.list);
	const rows = $derived(list === null ? [] : sortRows(mergeRows(list, store.data ?? []), sort.key, sort.dir));
	const waiting = $derived(store.data === null && store.error === null);

	function onsort(key: SortKey) {
		sort = nextSort(sort, key);
		saveSort(sort);
	}

	// Опрос живёт, пока открыт экран.
	onMount(() => {
		store.start();
		const t = setInterval(() => (now = new Date()), 30_000);
		return () => {
			clearInterval(t);
			store.stop();
		};
	});
</script>

<svelte:head><title>Обзор · pyrobot</title></svelte:head>

{#if list === null}
	<AccountsPending error={accounts.error} onretry={() => void accounts.load()} />
{:else if list.length === 0}
	<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
		<p>Аккаунтов пока нет.</p>
		<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
	</section>
{:else if waiting}
	<p class="card text-sm text-fg-muted" role="status">Загрузка…</p>
{:else}
	<div class="space-y-3">
		{#if store.error}
			<p class="text-sm text-bad-fg" role="alert">
				Сводка недоступна: {errorText(store.error)}.{store.data !== null ? ' Показаны прежние данные.' : ''}
			</p>
		{/if}
		<div class="hidden md:block">
			<SummaryTable {rows} {now} {sort} {onsort} />
		</div>
		<div class="md:hidden">
			<SummaryCards {rows} {now} />
		</div>
	</div>
{/if}
