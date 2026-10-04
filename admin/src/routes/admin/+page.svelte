<script lang="ts">
	import { AdminStore } from '$lib/admin/store.svelte';
	import { api, session } from '$lib/app.svelte';
	import AdminView from '$lib/components/admin/AdminView.svelte';

	const store = new AdminStore(api);
</script>

<svelte:head>
	<title>Сервер · pyrobot</title>
</svelte:head>

<h1 class="mb-3 text-lg font-semibold">Сервер</h1>
<AdminView {store} role={session.role} />
