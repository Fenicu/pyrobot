<script lang="ts">
	import type { PublicState } from '$lib/api/types';
	import { SKILL_MARK } from '$lib/util/game';
	import { val } from '$lib/util/observed';

	interface Props {
		state: PublicState;
		stale: string[];
	}
	let { state, stale }: Props = $props();

	const gadgets = $derived(val(state, 'gadgets'));
	const isStale = $derived(stale.includes('gadgets'));

	const bonuses = (b: Record<string, number>) =>
		Object.entries(b)
			.map(([skill, n]) => `+${n}${SKILL_MARK[skill] ?? skill}`)
			.join(' ');
</script>

<section class="card" aria-labelledby="gadgets-title">
	<h2 id="gadgets-title" class="card-title">Гаджеты</h2>
	<div class="text-sm {isStale ? 'text-fg-faint' : ''}" title={isStale ? 'устарело' : undefined}>
		{#if gadgets === null}
			<p class="text-fg-faint">нет данных</p>
		{:else if gadgets.items.length === 0}
			<p class="text-fg-muted">ничего не надето</p>
		{:else}
			<ul class="space-y-0.5">
				{#each gadgets.items as g, i (i)}
					<li>
						{g.slot} {g.name} {g.grade ?? ''}{g.level ?? ''}
						{#if Object.keys(g.bonuses).length > 0 || g.mark}·{/if}
						{bonuses(g.bonuses)}{#if g.mark}{` ${g.mark}`}{/if}
					</li>
				{/each}
			</ul>
			{#if gadgets.sets.length > 0}
				<p class="mt-2 text-fg-muted">{gadgets.sets.join(' · ')}</p>
			{/if}
		{/if}
		{#if isStale}<span class="sr-only"> (устарело)</span>{/if}
	</div>
</section>
