<script lang="ts">
	import { AdminStore } from '$lib/admin/store.svelte';
	import { api, session } from '$lib/app.svelte';
	import AdminView from '$lib/components/admin/AdminView.svelte';
	import Page from '$lib/components/shell/Page.svelte';

	const store = new AdminStore(api);
</script>

<Page title="Сервер">
	<AdminView {store} role={session.role} />
</Page>
