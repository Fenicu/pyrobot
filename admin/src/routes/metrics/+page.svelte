<script lang="ts">
	import { api } from '$lib/app.svelte';
	import MetricsView from '$lib/components/metrics/MetricsView.svelte';
</script>

<svelte:head><title>Метрики · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Метрики</h1>
<MetricsView {api} />
