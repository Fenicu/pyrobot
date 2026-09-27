<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		label: string;
		/** Значение устарело по политике свежести — приглушено, с подсказкой. */
		stale?: boolean;
		children: Snippet;
	}
	let { label, stale = false, children }: Props = $props();
</script>

<div class="flex items-baseline justify-between gap-3 py-0.5 text-sm">
	<span class="text-fg-muted">{label}</span>
	<span class="text-right tabular-nums {stale ? 'text-fg-faint' : ''}" title={stale ? 'устарело' : undefined}>
		{@render children()}{#if stale}<span class="sr-only"> (устарело)</span><span aria-hidden="true"> ·</span>{/if}
	</span>
</div>
