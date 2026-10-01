<script lang="ts">
	import { api, current } from '$lib/app.svelte';
	import ControlView from '$lib/components/control/ControlView.svelte';

	const ctx = current.get();
	const subscribe = ctx.live.subscribe.bind(ctx.live);
</script>

<svelte:head><title>Управление · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Управление</h1>
<ControlView api={ctx.api} globalApi={api} {subscribe} />
