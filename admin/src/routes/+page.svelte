<script lang="ts">
	import { goto } from '$app/navigation';
	import { accounts } from '$lib/app.svelte';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import { homeHref, lastAccount } from '$lib/nav';

	// «/» — последний открытый аккаунт, если он ещё есть, иначе первый, а без аккаунтов — их список.
	$effect(() => {
		if (accounts.list !== null) void goto(homeHref(accounts.list, lastAccount()), { replaceState: true });
	});
</script>

<svelte:head><title>pyrobot</title></svelte:head>

<AccountsPending error={accounts.list === null ? accounts.error : null} onretry={() => void accounts.load()} />
