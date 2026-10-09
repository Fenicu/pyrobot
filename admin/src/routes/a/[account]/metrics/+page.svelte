<script lang="ts">
	import { current } from '$lib/app.svelte';
	import MetricsView from '$lib/components/metrics/MetricsView.svelte';
	import Page from '$lib/components/shell/Page.svelte';

	const { api } = current.get();
</script>

<Page title="Метрики">
	<MetricsView {api} />
</Page>
