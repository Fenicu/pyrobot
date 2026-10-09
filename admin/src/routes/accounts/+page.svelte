<script lang="ts">
	import { accounts, api } from '$lib/app.svelte';
	import AccountsView from '$lib/components/accounts/AccountsView.svelte';
</script>

<AccountsView {api} store={accounts} />
