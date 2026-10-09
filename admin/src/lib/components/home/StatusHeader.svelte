<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { EngineStatus, PublicState } from '$lib/api/types';
	import { artifactIcon } from '$lib/artifact/text';
	import { fmtMoment, fmtRelative } from '$lib/util/format';
	import { busyText, tgStateLabel } from '$lib/util/game';
	import { val } from '$lib/util/observed';
	import Pill from '../Pill.svelte';

	interface Props {
		status: EngineStatus | null;
		error: ApiError | null;
		state: PublicState;
		now: Date;
	}
	let { status, error, state, now }: Props = $props();
	const busy = $derived(val(state, 'busy'));
	const collect = $derived(val(state, 'artifact_collect'));
	const levels = $derived(val(state, 'artifacts'));
</script>

<section class="flex flex-wrap items-center gap-1.5" aria-label="Статус">
	{#if status}
		{#if status.mode === 'live'}
			<Pill tone="ok">LIVE</Pill>
		{:else}
			<Pill tone="warn" title="Команды, кроме навигации, не уходят в игру">DRY RUN</Pill>
		{/if}
		{#if status.paused}<Pill tone="warn">пауза</Pill>{/if}
		{#if status.killed}
			<Pill tone="bad"><span class="ext-text">kill{status.kill_reason ? `: ${status.kill_reason}` : ''}</span></Pill>
		{/if}
		{#if status.spending_blocked}
			<Pill tone="bad"><span class="ext-text">траты заблокированы: {status.spending_blocked}</span></Pill>
		{/if}
		{#if status.running}
			<Pill tone={status.tg.state === 'online' ? 'ok' : 'bad'}>TG: {tgStateLabel(status.tg.state)}</Pill>
			{#if !status.lease_ok}<Pill tone="bad">нет аренды аккаунта</Pill>{/if}
			{#if !status.pipeline_healthy}<Pill tone="bad">конвейер нездоров</Pill>{/if}
			{#if !status.workers_ok}<Pill tone="bad">фоновая задача упала</Pill>{/if}
			{#if status.scenario}
				<Pill tone="dec">идёт: {status.scenario}</Pill>
			{/if}
			{#if status.next_wake}
				<Pill title={fmtRelative(status.next_wake, now)}>след. решение {fmtMoment(status.next_wake, now)}</Pill>
			{/if}
			{#if status.queue > 0}<Pill>в очереди: {status.queue}</Pill>{/if}
		{:else}
			<!-- Без движка проверки здоровья и TG — заглушки, а не сбой: причина — на плашке выше. -->
			<Pill>движок не запущен</Pill>
		{/if}
	{:else if error}
		<Pill tone="bad">{error.kind === 'engine_down' ? 'движок не запущен' : 'статус недоступен'}</Pill>
	{:else}
		<Pill>статус…</Pill>
	{/if}
	{#if collect && new Date(collect.ends_at) > now}
		<Pill tone="dec" title="Сбор артефакта до {fmtMoment(collect.ends_at, now)}">
			{artifactIcon(collect.artifact)} {levels?.[collect.artifact] ?? '?'}/100
		</Pill>
	{/if}
	{#if busy}
		<Pill title={fmtRelative(busy.until, now)}>{busyText(busy, now)}</Pill>
	{/if}
</section>
