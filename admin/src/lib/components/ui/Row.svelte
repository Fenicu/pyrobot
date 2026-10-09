<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		label: string;
		/** Значение устарело по политике свежести — приглушено, с подсказкой. */
		stale?: boolean;
		/** Строка списка определений: dt/dd, ставится внутрь <dl>. */
		dl?: boolean;
		children: Snippet;
	}
	let { label, stale = false, dl = false, children }: Props = $props();
</script>

{#snippet value()}
	{@render children()}{#if stale}<span class="sr-only"> (устарело)</span><span aria-hidden="true"> ·</span>{/if}
{/snippet}

{#if dl}
	<div class="grid grid-cols-[7rem_1fr] gap-2 border-b border-line-soft py-1 text-sm last:border-0">
		<dt class="text-fg-muted">{label}</dt>
		<dd class="min-w-0 {stale ? 'text-fg-faint' : ''}" title={stale ? 'устарело' : undefined}>{@render value()}</dd>
	</div>
{:else}
	<div class="flex items-baseline justify-between gap-3 border-b border-line-soft py-1 text-sm last:border-0">
		<span class="text-fg-muted">{label}</span>
		<span class="text-right tabular-nums {stale ? 'text-fg-faint' : ''}" title={stale ? 'устарело' : undefined}>
			{@render value()}
		</span>
	</div>
{/if}
