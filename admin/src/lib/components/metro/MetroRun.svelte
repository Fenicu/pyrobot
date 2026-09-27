<script lang="ts">
	import type { MetroRunDetail } from '$lib/api/types';
	import { EVENT_TEXT, eventCounts, frameAt, mapOf, OUTCOME_TEXT, outcomeOf } from '$lib/metro/model';
	import { fmtMoment, fmtNum, fmtSpan } from '$lib/util/format';
	import { CURRENCY } from '$lib/util/game';
	import Pill from '../Pill.svelte';
	import MetroMap from './MetroMap.svelte';
	import MetroPlayer from './MetroPlayer.svelte';

	interface Props {
		run: MetroRunDetail;
		now?: Date;
	}
	let { run, now = new Date() }: Props = $props();
	const model = $derived(mapOf(run));
	const max = $derived(Math.max(0, model.path.length - 1));
	let step = $state(0);
	$effect(() => {
		step = max;
	});
	const frame = $derived(frameAt(model, step));
	const outcome = $derived(outcomeOf(run));
	const result = $derived(Object.entries(run.result ?? {}));
</script>

<section class="space-y-3" aria-label="Забег #{run.id}">
	<div class="flex flex-wrap items-center gap-2">
		<h2 class="font-semibold">Забег #{run.id} · {fmtMoment(run.started_at, now)}</h2>
		<Pill tone={outcome === 'self' ? 'ok' : outcome === 'ejected' ? 'warn' : 'bad'}>{OUTCOME_TEXT[outcome]}</Pill>
		<span class="text-sm text-fg-muted">
			{run.steps} ш · {fmtSpan(run.duration_s)}{run.step_s ? ` · ${run.step_s.toFixed(1)} с/шаг` : ''}
			{#if model.visitedCount}· {(run.steps / model.visitedCount).toFixed(1)} ш/клетку{/if}
			· исход {run.outcome}
		</span>
	</div>

	<div class="grid gap-3 lg:grid-cols-[minmax(0,1fr)_18rem]">
		<div class="space-y-2">
			<MetroMap {model} {step} label="Карта забега #{run.id}: {run.steps} шагов, клеток {model.cells.length}" />
			{#if model.path.length > 0}
				<MetroPlayer {max} {step} onstep={(s) => (step = s)} />
				<p class="text-sm" aria-live="polite">
					шаг {step}/{max}
					{#if frame.pos}· клетка ({frame.pos[0]},{frame.pos[1]}){/if}
					{#if frame.vitals}· 🔋 {frame.vitals.stamina} · аптечки {frame.vitals.packs}{/if}
				</p>
			{/if}
			<p class="text-xs text-fg-faint">
				<span style:color="#e3b341">●</span> лут · <span style:color="#e5484d">●</span> бой / NPC ·
				<span style:color="#d9822b">●</span> сундук · <span style:color="#3fb950">●</span> выход · линия — маршрут · S/E —
				вход/выход
			</p>
		</div>
		<div class="card space-y-2 text-sm">
			<h3 class="card-title">Итог</h3>
			<p>бафы: {run.buffs.length ? run.buffs.join(', ') : '—'}</p>
			{#if result.length}
				<p class="flex flex-wrap gap-x-2">
					{#each result as [k, v] (k)}<span>{CURRENCY[k] ?? k} {fmtNum(Number(v))}</span>{/each}
				</p>
			{:else}
				<p class="text-fg-muted">итога нет</p>
			{/if}
			<p class="text-xs text-fg-muted">
				события: {eventCounts(model.events)
					.map(([k, n]) => `${EVENT_TEXT[k] ?? k} ×${n}`)
					.join(', ') || '—'}
			</p>
			{#if frame.events.length}
				<h4 class="text-xs text-fg-muted uppercase">К шагу {step}</h4>
				<ol class="max-h-48 space-y-0.5 overflow-y-auto text-xs">
					{#each frame.events.slice(-12).reverse() as e, i (i)}
						<li>
							<span class="font-mono text-fg-faint">{e.step}</span>
							{EVENT_TEXT[e.kind] ?? e.kind}
							{#if typeof e.enemy === 'string'}<span class="ext-text"> · {e.enemy}</span>{/if}
							{#if typeof e.item === 'string'}· {CURRENCY[e.item] ?? e.item} {e.amount ?? ''}{/if}
						</li>
					{/each}
				</ol>
			{/if}
		</div>
	</div>
</section>
