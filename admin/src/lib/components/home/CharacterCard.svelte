<script lang="ts">
	import type { PublicState } from '$lib/api/types';
	import { fmtCompact, fmtMoment, fmtNum, fmtRelative } from '$lib/util/format';
	import { activityLabel } from '$lib/util/game';
	import { val } from '$lib/util/observed';
	import Meter from '../Meter.svelte';
	import Row from './Row.svelte';

	interface Props {
		state: PublicState;
		stale: string[];
		now: Date;
	}
	let { state, stale, now }: Props = $props();

	const level = $derived(val(state, 'level'));
	const exp = $derived(val(state, 'exp'));
	const expNext = $derived(val(state, 'exp_next'));
	const motivation = $derived(val(state, 'motivation'));
	const motivationMax = $derived(val(state, 'motivation_max'));
	const nextAt = $derived(val(state, 'motivation_next_at'));
	const busy = $derived(val(state, 'busy'));
	const isStale = (field: string) => stale.includes(field);
</script>

<section class="card" aria-labelledby="character-title">
	<h2 id="character-title" class="card-title">Персонаж{level !== null ? ` · ур. ${level}` : ''}</h2>
	{#if Object.keys(state).length === 0}
		<p class="text-sm text-fg-muted">Снимка ещё нет: бот не видел ни одного экрана.</p>
	{:else}
		<Row label="💡 опыт" stale={isStale('exp')}>
			{fmtCompact(exp)} / {fmtCompact(expNext)}
		</Row>
		{#if exp !== null && expNext !== null}
			<Meter value={exp} max={expNext} label="Опыт до уровня" />
		{/if}
		<Row label="💵 деньги" stale={isStale('money')}>${fmtNum(val(state, 'money'))}</Row>
		<Row label="🔥 мотивация" stale={isStale('motivation')}>
			{fmtNum(motivation)} / {fmtNum(motivationMax)}
			{#if motivation !== null && motivationMax !== null && motivation >= motivationMax}
				· полная
			{:else if nextAt}
				· +1 {fmtRelative(nextAt, now)}
			{/if}
		</Row>
		<Row label="🔋 выносливость" stale={isStale('stamina')}>{fmtNum(val(state, 'stamina'))}</Row>
		<Row label="📚 🔩 ⚙️" stale={isStale('knowledge') || isStale('raw') || isStale('details')}>
			{fmtNum(val(state, 'knowledge'))} · {fmtNum(val(state, 'raw'))} · {fmtNum(val(state, 'details'))}
		</Row>
		<Row label="Занятость" stale={isStale('busy')}>
			{#if busy}
				{activityLabel(busy.activity)} до {fmtMoment(busy.until, now)}
			{:else}
				свободен
			{/if}
		</Row>
	{/if}
</section>
