<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { accounts, api } from '$lib/app.svelte';
	import { errorText } from '$lib/api/errors';
	import SearchAccounts from '$lib/components/overview/SearchAccounts.svelte';
	import SearchForm from '$lib/components/overview/SearchForm.svelte';
	import SearchResults from '$lib/components/overview/SearchResults.svelte';
	import { formatSearchParams, parseSearchParams, validateQuery, type SearchQuery } from '$lib/overview/search';
	import { SearchStore } from '$lib/overview/search.svelte';

	const initial = parseSearchParams(page.url.searchParams);
	const store = new SearchStore(api, initial);

	let now = $state(new Date());
	const empty = $derived(accounts.list !== null && accounts.list.length === 0);

	const accountsMap = $derived.by(() => {
		const map = new Map<number, string>();
		for (const a of accounts.list ?? []) {
			map.set(a.id, a.name);
		}
		for (const a of store.result?.accounts ?? []) {
			if (!map.has(a.account_id)) {
				map.set(a.account_id, a.name);
			}
		}
		return map;
	});

	function updateUrl(query: SearchQuery) {
		const sp = formatSearchParams(query);
		const qs = sp.toString();
		const href = qs ? `${page.url.pathname}?${qs}` : page.url.pathname;
		void goto(href, { replaceState: true, noScroll: true, keepFocus: true });
	}

	async function handleSearch(params: SearchQuery) {
		updateUrl(params);
		await store.search(params);
	}

	onMount(() => {
		const timer = setInterval(() => (now = new Date()), 30_000);
		// Если в URL уже есть валидный запрос (например, переход по ссылке), запускаем поиск сразу
		if (validateQuery(initial.q) === null) {
			void store.search(initial);
		}
		return () => {
			clearInterval(timer);
			store.abort();
		};
	});

	onDestroy(() => {
		store.abort();
	});
</script>

<svelte:head>
	<title>Поиск · pyrobot</title>
</svelte:head>

{#if empty}
	<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
		<p>Аккаунтов пока нет.</p>
		<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
	</section>
{:else}
	<div class="space-y-3">
		<SearchForm
			q={store.q}
			where={store.where}
			accountIds={store.accountIds}
			limit={store.limit}
			accounts={accounts.list}
			loading={store.loading}
			validationError={store.validationError}
			onsubmit={handleSearch}
		/>

		{#if store.error}
			<p class="card text-sm text-bad-fg" role="alert">
				Ошибка поиска: {errorText(store.error)}.
			</p>
		{/if}

		{#if store.loading && store.where === 'db'}
			<p class="card text-sm text-fg-muted" role="status">
				Поиск по базе…
			</p>
		{/if}

		{#if store.result && !store.loading}
			<SearchAccounts accounts={store.result.accounts} />
			<SearchResults hits={store.result.hits} {accountsMap} {now} />
		{/if}
	</div>
{/if}
