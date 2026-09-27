<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/app.svelte';
	import MetroView from '$lib/components/metro/MetroView.svelte';

	const raw = page.url.searchParams.get('run');
	const initial = raw && /^\d+$/.test(raw) ? Number(raw) : null;
</script>

<svelte:head><title>Метро · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Метро</h1>
<MetroView
	{api}
	{initial}
	onselect={(id) => void goto(`/metro?run=${id}`, { replaceState: true, keepFocus: true, noScroll: true })}
/>
