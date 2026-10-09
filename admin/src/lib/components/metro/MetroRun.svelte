<script lang="ts">
	import type { MetroRunDetail } from '$lib/api/types';
	import {
		EVENT_TEXT,
		eventCounts,
		eventIcon,
		eventText,
		frameAt,
		mapOf,
		OUTCOME_TEXT,
		outcomeOf,
		timeline
	} from '$lib/metro/model';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment, fmtNum, fmtSpan } from '$lib/util/format';
	import { CURRENCY } from '$lib/util/game';
	import Pill from '../Pill.svelte';
	import Card from '../ui/Card.svelte';
	import MetroMap from './MetroMap.svelte';
	import MetroPlayer from './MetroPlayer.svelte';

	interface Props {
		run: MetroRunDetail;
		now?: Date;
	}
	let { run, now: fixedNow }: Props = $props();
	const now = $derived(fixedNow ?? clock.now);
	const model = $derived(mapOf(run));
	const max = $derived(Math.max(0, model.path.length - 1));
	let step = $state(0);
	$effect(() => {
		step = max;
	});
	const frame = $derived(frameAt(model, step));
	const events = $derived(timeline(model));
	const recent = $derived(events.filter((e) => e.step <= step).slice(-12).reverse());
	const outcome = $derived(outcomeOf(run));
	const result = $derived(Object.entries(run.result ?? {}));
</script>

<section
	class="grid items-start gap-[14px] lg:grid-cols-[minmax(0,1fr)_20rem] xl:grid-cols-[minmax(0,40rem)_minmax(20rem,1fr)]"
	aria-label="Забег #{run.id}"
>
	<div class="card min-w-0 space-y-2">
		<div class="flex flex-wrap items-center gap-2">
			<h2 class="font-semibold">Забег #{run.id} · {fmtMoment(run.started_at, now)}</h2>
			<Pill tone={outcome === 'self' ? 'ok' : outcome === 'ejected' ? 'warn' : 'bad'}>{OUTCOME_TEXT[outcome]}</Pill>
			<span class="text-sm text-fg-muted">
				{run.steps} ш · {fmtSpan(run.duration_s)}{run.step_s ? ` · ${run.step_s.toFixed(1)} с/шаг` : ''}
				{#if model.visitedCount}· {(run.steps / model.visitedCount).toFixed(1)} ш/клетку{/if}
				· исход {run.outcome}
			</span>
		</div>
		<MetroMap {model} {step} label="Карта забега #{run.id}: {run.steps} шагов, посещено клеток {model.visitedCount}" />
		{#if model.path.length > 0}
			<MetroPlayer {max} {step} onstep={(s) => (step = s)} />
			<p class="text-sm" aria-live="polite">
				шаг {step}/{max}
				{#if frame.pos}· клетка ({frame.pos[0]},{frame.pos[1]}){/if}
				{#if frame.vitals}· 🔋 {frame.vitals.stamina} · аптечки {frame.vitals.packs}{/if}
			</p>
		{/if}
		<p class="text-xs text-fg-faint" aria-label="Легенда карты">
			💵📚🍕… находка · ⚔️ бой (💀 — проигран) · 👤 NPC без боя · 📦 тайник (бледный — не открыт) · 🏹💥 ловушка ·
			❤️ аптечка · 🚇 вход · 🚪 выход · светлее — пройдено, туман — ещё не видно · яркая линия — последние шаги
		</p>
	</div>
	<Card title="Итог" bodyClass="space-y-2 text-sm">
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
		{#if recent.length}
			<h3 class="text-xs text-fg-muted uppercase">К шагу {step}</h3>
			<ol class="max-h-48 space-y-0.5 overflow-y-auto text-xs" aria-label="События к шагу {step}">
				{#each recent as e, i (i)}
					<li class="flex gap-1.5">
						<span class="w-7 shrink-0 text-right font-mono text-fg-faint">{e.step}</span>
						<span class="w-4 shrink-0 text-center" aria-hidden="true">{eventIcon(e) ?? '•'}</span>
						<span class="ext-text min-w-0">{eventText(e)}</span>
					</li>
				{/each}
			</ol>
		{/if}
	</Card>
</section>
