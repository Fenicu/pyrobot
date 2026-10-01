<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { current } from '$lib/app.svelte';
	import MetroView from '$lib/components/metro/MetroView.svelte';
	import { accountHref } from '$lib/nav';

	const { id: account, api } = current.get();

	const raw = page.url.searchParams.get('run');
	const initial = raw && /^\d+$/.test(raw) ? Number(raw) : null;
</script>

<svelte:head><title>Метро · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Метро</h1>
<MetroView
	{api}
	{initial}
	onselect={(id) =>
		void goto(accountHref(account, `/metro?run=${id}`), { replaceState: true, keepFocus: true, noScroll: true })}
/>
