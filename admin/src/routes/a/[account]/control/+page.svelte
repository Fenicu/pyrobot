<script lang="ts">
	import { api, current } from '$lib/app.svelte';
	import ControlView from '$lib/components/control/ControlView.svelte';

	const ctx = current.get();
	const subscribe = ctx.live.subscribe.bind(ctx.live);
</script>

<ControlView api={ctx.api} globalApi={api} {subscribe} />
