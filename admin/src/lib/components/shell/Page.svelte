<script lang="ts">
	import type { Snippet } from 'svelte';
	import ConnectionDot from '../ConnectionDot.svelte';
	import PageHeader from '../ui/PageHeader.svelte';
	import { accountFrame } from './frame';

	interface Props {
		/** Раздел: на экранах аккаунта — после имени аккаунта. */
		title: string;
		status?: Snippet;
		actions?: Snippet;
		children: Snippet;
	}
	let { title, status, actions, children }: Props = $props();
	const frame = accountFrame();
	const f = $derived(frame());
</script>

<svelte:head><title>{title} · pyrobot</title></svelte:head>

{#snippet statusRow()}
	{@render status?.()}
	{#if f}<ConnectionDot status={f.live} retryIn={f.retryIn} stopped={f.stopped} compact />{/if}
{/snippet}

<PageHeader
	title={f?.title ?? title}
	subtitle={f ? title : undefined}
	status={status || f ? statusRow : undefined}
	{actions}
/>
<div class="p-3.5">
	{#if f}{@render f.banners()}{/if}
	{@render children()}
</div>
