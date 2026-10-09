<script lang="ts">
	import { AdminStore } from '$lib/admin/store.svelte';
	import { api, session } from '$lib/app.svelte';
	import AdminView from '$lib/components/admin/AdminView.svelte';

	const store = new AdminStore(api);
</script>

<AdminView {store} role={session.role} />
