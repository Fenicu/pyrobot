<script lang="ts">
	import type { SearchAccountOut } from '$lib/api/types';
	import { reasonText } from '$lib/overview/search';

	interface Props {
		accounts: SearchAccountOut[];
	}
	let { accounts }: Props = $props();
</script>

<div class="flex flex-wrap items-center gap-1.5" role="region" aria-label="Статусы аккаунтов">
	{#each accounts as a (a.account_id)}
		{#if a.status === 'ok'}
			<span class="pill pill-ok" title="Найдено: {a.count}">
				{a.name}: {a.count}
			</span>
		{:else if a.status === 'unavailable'}
			{@const text = reasonText(a.reason)}
			<span class="pill pill-warn" title="{a.name}: {text}">
				{a.name}: {text}
			</span>
		{:else}
			{@const text = reasonText(a.reason)}
			<span class="pill pill-bad" title="{a.name}: {text}">
				{a.name}: {text}
			</span>
		{/if}
	{/each}
</div>
