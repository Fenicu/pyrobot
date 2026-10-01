<script lang="ts">
	import { accounts, api } from '$lib/app.svelte';
	import AccountsView from '$lib/components/accounts/AccountsView.svelte';
</script>

<svelte:head><title>Аккаунты · pyrobot</title></svelte:head>

<h1 class="mb-3 text-lg font-semibold">Аккаунты</h1>
<AccountsView {api} store={accounts} />
