<script lang="ts">
	import { onMount, type Snippet } from 'svelte';
	import ConnectionDot from '../ConnectionDot.svelte';
	import StatusHeader from '../home/StatusHeader.svelte';
	import PageHeader from '../ui/PageHeader.svelte';
	import { accountFrame, pageTitleSink } from './frame';

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
	const publishTitle = pageTitleSink();
	// «след. решение через …» и «уже свободен» — по часам страницы, раз в 30 с.
	let now = $state(new Date());

	$effect(() => publishTitle(title));

	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		return () => clearInterval(t);
	});
</script>

<svelte:head><title>{title} · pyrobot</title></svelte:head>

{#snippet statusRow()}
	{#if f}<StatusHeader status={f.engine} error={f.engineError} state={f.state} {now} />{/if}
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
