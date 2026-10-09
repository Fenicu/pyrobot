<script lang="ts">
	import { api, current } from '$lib/app.svelte';
	import ControlView from '$lib/components/control/ControlView.svelte';
	import Page from '$lib/components/shell/Page.svelte';

	const ctx = current.get();
	const subscribe = ctx.live.subscribe.bind(ctx.live);
</script>

<Page title="Управление">
	<ControlView api={ctx.api} globalApi={api} {subscribe} />
</Page>
