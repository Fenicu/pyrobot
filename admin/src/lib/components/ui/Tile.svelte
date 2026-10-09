<script lang="ts">
	import type { Component } from 'svelte';

	interface Props {
		href?: string;
		icon: Component<{ class?: string; 'aria-hidden'?: boolean | 'true' }>;
		label: string;
		badge?: number;
		onclick?: () => void;
	}
	let { href, icon: Icon, label, badge = 0, onclick }: Props = $props();

	const cls =
		'relative flex min-h-16 flex-col items-center justify-center gap-1 rounded-ctl bg-surface-2 px-1 py-2.5 text-center text-xs text-fg transition-colors hover:bg-accent-soft';
</script>

{#snippet body()}
	<Icon class="size-5 text-fg-muted" aria-hidden="true" />
	<span class="leading-tight">{label}</span>
	{#if badge > 0}
		<span
			class="absolute top-1.5 right-2 min-w-4 rounded-full bg-bad px-1 text-[10px] leading-4 font-semibold text-white tabular-nums"
			>{badge}</span
		>
	{/if}
{/snippet}

{#if href}
	<a {href} class={cls} {onclick}>{@render body()}</a>
{:else}
	<button type="button" class={cls} {onclick}>{@render body()}</button>
{/if}
