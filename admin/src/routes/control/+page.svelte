<script lang="ts">
	import { accountApi, api, live } from '$lib/app.svelte';
	import ControlView from '$lib/components/control/ControlView.svelte';

	const subscribe = live.subscribe.bind(live);
</script>

<svelte:head><title>Управление · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Управление</h1>
<ControlView api={accountApi} globalApi={api} {subscribe} />
