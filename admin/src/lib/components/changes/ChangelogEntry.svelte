<script lang="ts">
	import type { ChangelogEntry } from '$lib/changelog';

	interface Props {
		entry: ChangelogEntry;
		/** Уровень заголовка версии: в окне — под заголовком окна, на странице — под h1. */
		heading?: 'h2' | 'h3';
		installed?: boolean;
	}

	let { entry, heading = 'h3', installed = false }: Props = $props();
</script>

<div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
	<svelte:element this={heading} class="text-base font-semibold">{entry.version}</svelte:element>
	{#if installed}<span class="pill pill-ok">установлена</span>{/if}
	{#if entry.date}<span class="text-xs text-fg-muted">{entry.date}</span>{/if}
</div>
{#each entry.sections as section, i (i)}
	<div class="mt-2">
		<div class="label">{section.title}</div>
		<ul class="mt-1 list-disc space-y-1 pl-5 text-sm">
			{#each section.items as item, j (j)}
				<li class="ext-text">{item}</li>
			{/each}
		</ul>
	</div>
{/each}
{#each entry.preamble as line, i (i)}
	<p class="ext-text mt-2 text-sm">{line}</p>
{/each}
