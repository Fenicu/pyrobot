<script lang="ts">
	import { current } from '$lib/app.svelte';
	import MetricsView from '$lib/components/metrics/MetricsView.svelte';

	const { api } = current.get();
</script>

<MetricsView {api} />
