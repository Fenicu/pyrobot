<script lang="ts">
	import type { PublicState } from '$lib/api/types';
	import { fmtMoment, fmtNum, mskDay, toDate } from '$lib/util/format';
	import { activityLabel, CURRENCY, GORBUSHKA, LEVEL, PERSONAL_TASK } from '$lib/util/game';
	import { val } from '$lib/util/observed';
	import Meter from '../Meter.svelte';
	import Pill from '../Pill.svelte';
	import Card from '../ui/Card.svelte';
	import Row from '../ui/Row.svelte';

	interface Props {
		state: PublicState;
		stale: string[];
		now: Date;
	}
	let { state, stale, now }: Props = $props();

	const today = $derived(mskDay(now));
	const personal = $derived(val(state, 'daily_personal'));
	const team = $derived(val(state, 'team_task'));
	const lottery = $derived(val(state, 'lottery'));
	const battleAt = $derived(val(state, 'battle_at'));
	const target = $derived(val(state, 'battle_target'));
	const metroAt = $derived(val(state, 'metro_ready_at'));
	const metroRun = $derived(val(state, 'metro_message'));
	const gorbushka = $derived(val(state, 'gorbushka'));
	const isToday = (day: string | null | undefined) => day === today;
	const past = (at: string | null | undefined) => {
		const d = toDate(at);
		return d !== null && d.getTime() <= now.getTime();
	};
	const lotteryParts = $derived(
		lottery?.limits
			? Object.entries(lottery.limits).map(([cur, limit]) => ({
					cur,
					bought: lottery.bought?.[cur] ?? null,
					limit
				}))
			: []
	);
	const lotteryDone = $derived(
		lotteryParts.length > 0 && lotteryParts.every((p) => p.bought !== null && p.bought >= p.limit)
	);
</script>

<Card title="Сегодня">
	<Row label="Личное задание" stale={stale.includes('daily_personal')}>
		{#if !personal || !isToday(personal.day)}
			<span class="text-fg-faint">нет данных за сегодня</span>
		{:else if personal.status === 'done'}
			{#if personal.chosen?.type}
				{PERSONAL_TASK[personal.chosen.type] ?? personal.chosen.type}
				{personal.chosen.level ? LEVEL[personal.chosen.level] ?? personal.chosen.level : ''} ·
			{/if}
			<Pill tone="ok">✓ выполнено</Pill>
		{:else if personal.status === 'active' && personal.chosen}
			{PERSONAL_TASK[personal.chosen.type ?? ''] ?? personal.chosen.type} · {fmtNum(personal.current)}/{fmtNum(
				personal.chosen.goal
			)}
		{:else}
			выбор из {personal.offers?.length ?? 0}
		{/if}
	</Row>
	{#if personal && isToday(personal.day) && personal.status === 'active' && personal.chosen}
		<Meter value={personal.current ?? 0} max={personal.chosen.goal ?? 0} label="Личное задание" />
	{/if}

	<Row label="Командное{team?.resource ? ` · ${team.resource}` : ''}" stale={stale.includes('team_task')}>
		{#if !team || (team.day && !isToday(team.day))}
			<span class="text-fg-faint">нет данных за сегодня</span>
		{:else if team.status === 'none'}
			нет задания
		{:else if team.status === 'offers'}
			не выбрано · вариантов {team.offers?.length ?? 0}
		{:else if team.status === 'done'}
			{fmtNum(team.current)}/{fmtNum(team.goal)} <Pill tone="ok">✓</Pill>
		{:else}
			{fmtNum(team.current)}/{fmtNum(team.goal)}
		{/if}
	</Row>
	{#if team && team.status === 'active' && (!team.day || isToday(team.day))}
		<Meter value={team.current} max={team.goal} label="Командное задание" />
		{#if team.activities?.length}
			<p class="text-xs text-fg-faint">дела: {team.activities.map(activityLabel).join(', ')}</p>
		{/if}
	{/if}

	<Row label="Лотерея{lottery ? ` · тираж ${lottery.draw}` : ''}" stale={stale.includes('lottery')}>
		{#if !lottery}
			<span class="text-fg-faint">—</span>
		{:else if lotteryParts.length === 0}
			продажа до {fmtMoment(lottery.until, now)}
		{:else}
			{#each lotteryParts as p (p.cur)}
				<span class="ml-1.5 whitespace-nowrap">{CURRENCY[p.cur] ?? p.cur} {p.bought ?? '?'}/{p.limit}</span>
			{/each}
			{#if lotteryDone}<Pill tone="ok">✓</Pill>{/if}
		{/if}
	</Row>
	{#if lottery && !lotteryDone}
		<p class="text-right text-xs text-fg-faint">
			{past(lottery.until) ? 'продажа закрыта' : `продажа до ${fmtMoment(lottery.until, now)}`}
		</p>
	{/if}

	<Row label="Битва{battleAt ? ` ${fmtMoment(battleAt, now)}` : ''}" stale={stale.includes('battle_at')}>
		{target ?? '—'}
	</Row>

	<Row label="Метро" stale={stale.includes('metro_ready_at')}>
		{#if metroRun}
			<Pill tone="dec">идёт забег</Pill>
		{:else if !metroAt}
			—
		{:else if past(metroAt)}
			доступно
		{:else}
			доступно в {fmtMoment(metroAt, now)}
		{/if}
	</Row>

	<Row label="Горбушка" stale={stale.includes('gorbushka')}>
		{#if !gorbushka}
			—
		{:else}
			{GORBUSHKA[gorbushka.state] ?? gorbushka.state}
			{#if gorbushka.won !== null && gorbushka.won !== undefined && gorbushka.total}
				· бои {gorbushka.won}/{gorbushka.total}
			{/if}
			{#if gorbushka.state === 'done' && gorbushka.comeback_at}
				· снова {fmtMoment(gorbushka.comeback_at, now)}
			{:else if gorbushka.next_fight_at && !past(gorbushka.next_fight_at)}
				· бой {fmtMoment(gorbushka.next_fight_at, now)}
			{/if}
			{#if gorbushka.ticket_until && !past(gorbushka.ticket_until)}
				· билет до {fmtMoment(gorbushka.ticket_until, now)}
			{/if}
		{/if}
	</Row>
</Card>
