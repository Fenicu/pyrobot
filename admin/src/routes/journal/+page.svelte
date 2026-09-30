<script lang="ts">
	import { onMount } from 'svelte';
	import { accountApi, live } from '$lib/app.svelte';
	import JournalView from '$lib/components/journal/JournalView.svelte';
	import { JournalFeed } from '$lib/stores/journal.svelte';

	const feed = new JournalFeed(accountApi);
	const subscribe = live.subscribe.bind(live);

	onMount(() => {
		void feed.reload();
		return live.subscribe((e) => feed.onEvent(e));
	});
</script>

<svelte:head><title>Журнал · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Журнал</h1>
<JournalView api={accountApi} {feed} {subscribe} />
