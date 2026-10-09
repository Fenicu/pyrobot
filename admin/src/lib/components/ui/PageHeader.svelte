<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title: string;
		subtitle?: string;
		status?: Snippet;
		actions?: Snippet;
	}
	let { title, subtitle, status, actions }: Props = $props();
</script>

<header class="flex min-h-[50px] flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-line px-3.5 py-2">
	<h1 class="mr-2 text-base font-semibold md:text-[15px]">
		{title}{#if subtitle}<span class="font-normal text-fg-muted">{` · ${subtitle}`}</span>{/if}
	</h1>
	{#if status}
		<div class="flex flex-wrap items-center gap-1.5">{@render status()}</div>
	{/if}
	{#if actions}
		<div class="ml-auto flex flex-wrap items-center gap-2">{@render actions()}</div>
	{/if}
</header>
