<script lang="ts">
	import Search from '@lucide/svelte/icons/search';
	import type { ScenarioInfo } from '$lib/api/types';
	import Pill from '../Pill.svelte';

	interface Props {
		scenarios: ScenarioInfo[];
		selected: string | null;
		onselect: (name: string) => void;
	}
	let { scenarios, selected, onselect }: Props = $props();
	let query = $state('');
	const shown = $derived(
		scenarios.filter((s) => s.name.toLowerCase().includes(query.trim().toLowerCase()))
	);
</script>

<section class="card" aria-labelledby="catalog-title">
	<h2 id="catalog-title" class="card-title">Сценарии</h2>
	<label class="relative mb-2 block">
		<span class="sr-only">Поиск сценария</span>
		<Search class="pointer-events-none absolute top-2.5 left-2 size-4 text-fg-faint" aria-hidden="true" />
		<input class="input pl-8" type="search" placeholder="поиск…" bind:value={query} />
	</label>
	<ul class="max-h-[60vh] overflow-y-auto" aria-label="Каталог сценариев">
		{#each shown as s (s.name)}
			<li>
				<button
					type="button"
					class="flex w-full items-center justify-between gap-2 border-b border-line-soft px-2 py-1.5 text-left text-sm hover:bg-surface-2 {selected ===
					s.name
						? 'bg-accent-soft'
						: ''}"
					aria-pressed={selected === s.name}
					onclick={() => onselect(s.name)}
				>
					<span class="min-w-0 truncate font-mono text-xs">{s.name}</span>
					<span class="flex shrink-0 items-center gap-1">
						{#if Object.keys(s.required).length > 0}
							<span class="text-xs text-fg-faint">{Object.keys(s.required).length} пар.</span>
						{/if}
						{#if s.certified}
							<Pill tone="ok" title="Сертифицирован на настоящих кадрах">серт.</Pill>
						{:else}
							<Pill title="Не-nav шаги подавляются (simulate)">симуляция</Pill>
						{/if}
					</span>
				</button>
			</li>
		{:else}
			<li class="px-2 py-1.5 text-sm text-fg-muted">Ничего не найдено.</li>
		{/each}
	</ul>
</section>
