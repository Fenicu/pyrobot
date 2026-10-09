<script lang="ts">
	import { accounts, api } from '$lib/app.svelte';
	import AccountsView from '$lib/components/accounts/AccountsView.svelte';
	import Page from '$lib/components/shell/Page.svelte';
</script>

<Page title="Аккаунты">
	<AccountsView {api} store={accounts} />
</Page>
