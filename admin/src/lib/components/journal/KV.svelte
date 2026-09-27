<script lang="ts">
	import type { Snippet } from 'svelte';

	let { label, children }: { label: string; children: Snippet } = $props();
</script>

<div class="grid grid-cols-[7rem_1fr] gap-2 py-0.5 text-sm">
	<dt class="text-fg-muted">{label}</dt>
	<dd class="min-w-0">{@render children()}</dd>
</div>
