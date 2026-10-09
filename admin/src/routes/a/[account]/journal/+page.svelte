<script lang="ts">
	import { onMount } from 'svelte';
	import { current } from '$lib/app.svelte';
	import JournalView from '$lib/components/journal/JournalView.svelte';
	import Page from '$lib/components/shell/Page.svelte';
	import { JournalFeed } from '$lib/stores/journal.svelte';

	const { api, live } = current.get();
	const feed = new JournalFeed(api);
	const subscribe = live.subscribe.bind(live);

	onMount(() => {
		void feed.reload();
		return live.subscribe((e) => feed.onEvent(e));
	});
</script>

<Page title="Журнал">
	<JournalView {api} {feed} {subscribe} />
</Page>
