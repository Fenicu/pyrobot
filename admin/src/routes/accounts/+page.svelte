<script lang="ts">
	import { accounts } from '$lib/app.svelte';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import { accountHref } from '$lib/nav';
</script>

<svelte:head><title>Аккаунты · pyrobot</title></svelte:head>

<!-- Пока только список со ссылками: сюда ведут «/» без аккаунтов и неизвестный аккаунт в адресе. -->
<h1 class="mb-3 text-lg font-semibold">Аккаунты</h1>
{#if accounts.list === null}
	<AccountsPending error={accounts.error} onretry={() => void accounts.load()} />
{:else if accounts.list.length === 0}
	<p class="text-sm text-fg-muted">Аккаунтов нет.</p>
{:else}
	<ul class="card max-w-md space-y-1">
		{#each accounts.list as a (a.id)}
			<li><a class="text-accent hover:underline" href={accountHref(a.id, '')}>{a.name}</a></li>
		{/each}
	</ul>
{/if}
