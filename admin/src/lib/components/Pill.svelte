<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		tone?: 'ok' | 'warn' | 'bad' | 'muted' | 'dec' | 'msg';
		title?: string;
		children: Snippet;
	}
	let { tone = 'muted', title, children }: Props = $props();
</script>

<span class="pill pill-{tone}" {title}>{@render children()}</span>
