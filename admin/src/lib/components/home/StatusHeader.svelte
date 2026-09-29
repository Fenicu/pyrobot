<script lang="ts">
	import type { ApiError } from '$lib/api/errors';
	import type { EngineStatus, PublicState } from '$lib/api/types';
	import type { LiveStatus } from '$lib/live/connection.svelte';
	import { fmtMoment, fmtRelative } from '$lib/util/format';
	import { busyText, tgStateLabel } from '$lib/util/game';
	import { val } from '$lib/util/observed';
	import ConnectionDot from '../ConnectionDot.svelte';
	import Pill from '../Pill.svelte';

	interface Props {
		status: EngineStatus | null;
		error: ApiError | null;
		live: LiveStatus;
		retryIn?: number;
		state: PublicState;
		now: Date;
	}
	let { status, error, live, retryIn = 0, state, now }: Props = $props();
	const busy = $derived(val(state, 'busy'));
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
		<Pill tone={status.tg.state === 'online' ? 'ok' : 'bad'}>TG: {tgStateLabel(status.tg.state)}</Pill>
		{#if !status.lock_ok}<Pill tone="bad">нет блокировки экземпляра</Pill>{/if}
		{#if !status.pipeline_healthy}<Pill tone="bad">конвейер нездоров</Pill>{/if}
		{#if !status.workers_ok}<Pill tone="bad">фоновая задача упала</Pill>{/if}
		{#if status.scenario}
			<Pill tone="dec">идёт: {status.scenario}</Pill>
		{/if}
		{#if status.next_wake}
			<Pill title={fmtRelative(status.next_wake, now)}>след. решение {fmtMoment(status.next_wake, now)}</Pill>
		{/if}
		{#if status.queue > 0}<Pill>в очереди: {status.queue}</Pill>{/if}
	{:else if error}
		<Pill tone="bad">{error.kind === 'engine_down' ? 'движок не запущен' : 'статус недоступен'}</Pill>
	{:else}
		<Pill>статус…</Pill>
	{/if}
	{#if busy}
		<Pill title={fmtRelative(busy.until, now)}>{busyText(busy, now)}</Pill>
	{/if}
	<span class="ml-auto"><ConnectionDot status={live} {retryIn} /></span>
</section>
