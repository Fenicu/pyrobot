<script lang="ts">
	import type { Snippet } from 'svelte';

	interface Props {
		title?: string;
		action?: Snippet;
		children: Snippet;
		class?: string;
		bodyClass?: string;
	}
	let { title, action, children, class: klass = '', bodyClass = '' }: Props = $props();
	const titleId = $props.id();
</script>

<section class="card min-w-0 {klass}" aria-labelledby={title ? titleId : undefined}>
	{#if title || action}
		<div class="mb-2 flex items-center justify-between gap-2">
			{#if title}<h2 id={titleId} class="card-title mb-0">{title}</h2>{/if}
			{#if action}<div class="ml-auto shrink-0 text-xs">{@render action()}</div>{/if}
		</div>
	{/if}
	<div class={bodyClass}>{@render children()}</div>
</section>
