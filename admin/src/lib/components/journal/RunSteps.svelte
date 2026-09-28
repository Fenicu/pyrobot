<script lang="ts">
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { ActionOut, ScenarioRunDetail } from '$lib/api/types';
	import { isActionCreated, type LiveEvent, type SseActionCreated } from '$lib/live/sse';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment, fmtSpan, toDate } from '$lib/util/format';
	import { ACTION_STATUS, statusTone } from '$lib/util/game';
	import { pretty } from '$lib/util/text';
	import Pill from '../Pill.svelte';

	interface Props {
		api: Api;
		runId: number;
		/** Подписка на поток: кадр `scenario_run` этого запуска перечитывает его, кадры `action` его
		 * шагов добавляют и обновляют шаги сразу, а пока запуск идёт — ещё и перечитывают его. */
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
	}
	let { api, runId, subscribe }: Props = $props();
	let run = $state<ScenarioRunDetail | null>(null);
	let error = $state('');
	const now = $derived(clock.now);

	// Номер чтения: ответ, который обогнало более новое чтение, не применяется.
	let request = 0;

	async function load(id: number) {
		const mine = ++request;
		try {
			const out = await call(api.GET('/api/v1/scenario-runs/{run_id}', { params: { path: { run_id: id } } }));
			if (mine !== request) return;
			run = out;
			error = '';
		} catch (e) {
			if (mine === request) error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void load(runId);
	});

	/** Шаг из кадра создания — до ответа API (момент — приход кадра). */
	function stepOf(d: SseActionCreated): ActionOut {
		return {
			id: d.id,
			created_at: new Date().toISOString(),
			source: d.source,
			kind: d.kind,
			chat_id: d.chat_id,
			payload: { text: d.text, data: d.data },
			command_class: d.command_class,
			status: d.status,
			reason: d.reason,
			attempts: 0,
			answer: null,
			match_detail: null,
			sent_at: null,
			finished_at: null,
			reconciled_at: null,
			idempotency_key: null,
			scenario_run_id: d.scenario_run_id
		};
	}

	function onEvent(e: LiveEvent) {
		if (e.type === 'scenario_run') {
			if (e.data.id === runId) void load(runId);
			return;
		}
		if (e.type !== 'action') return;
		const d = e.data;
		const mine = isActionCreated(d) ? d.scenario_run_id === runId : !!run?.actions.some((a) => a.id === d.id);
		if (!mine) return;
		if (run === null) {
			void load(runId);
			return;
		}
		const i = run.actions.findIndex((a) => a.id === d.id);
		const row = run.actions[i];
		if (isActionCreated(d)) {
			if (i === -1) run = { ...run, actions: [...run.actions, stepOf(d)] };
		} else if (row) {
			run = { ...run, actions: run.actions.with(i, { ...row, status: d.status, reason: d.reason || row.reason }) };
		}
		if (run.status === 'queued' || run.status === 'running') void load(runId);
	}

	$effect(() => {
		if (!subscribe) return;
		return subscribe(onEvent);
	});

	const duration = $derived.by(() => {
		const a = toDate(run?.started_at);
		const b = toDate(run?.finished_at);
		return a && b ? fmtSpan((b.getTime() - a.getTime()) / 1000) : null;
	});
</script>

<div class="rounded-md border border-line-soft p-2">
	{#if error}
		<p class="ext-text text-sm text-bad-fg">{error}</p>
	{:else if !run}
		<p class="text-sm text-fg-muted">Запуск #{runId}…</p>
	{:else}
		<div class="flex flex-wrap items-center gap-1.5 text-sm">
			<span class="font-medium">Запуск #{run.id} · {run.scenario}</span>
			<Pill tone={statusTone(run.status)}>{run.status}</Pill>
			{#if run.reason}<span class="ext-text text-fg-muted">{run.reason}</span>{/if}
		</div>
		<p class="mt-0.5 text-xs text-fg-faint">
			{fmtMoment(run.started_at, now, true)}{duration ? ` · ${duration}` : ''}{run.requested_by
				? ` · вручную: ${run.requested_by}`
				: ''}
			{#if run.metro_run_id}
				· <a class="text-accent underline" href="/metro?run={run.metro_run_id}">забег метро #{run.metro_run_id}</a>
			{/if}
		</p>
		{#if Object.keys(run.params).length > 0}
			<pre class="ext-text mt-1 rounded bg-bg p-1.5 font-mono text-xs">{pretty(run.params)}</pre>
		{/if}
		{#if run.actions.length > 0}
			<ol class="mt-1.5 space-y-1" aria-label="Шаги запуска #{run.id}">
				{#each run.actions as a (a.id)}
					<li class="flex items-baseline gap-2 text-xs">
						<span class="font-mono text-fg-faint">{fmtMoment(a.created_at, now, true)}</span>
						<span class="ext-text min-w-0 flex-1 truncate">{a.payload.text ?? a.payload.data ?? '—'}</span>
						<Pill tone={statusTone(a.status)}>{ACTION_STATUS[a.status] ?? a.status}</Pill>
						{#if a.reason}<span class="ext-text text-fg-muted">{a.reason}</span>{/if}
					</li>
				{/each}
			</ol>
		{:else}
			<p class="mt-1 text-xs text-fg-faint">Шагов нет.</p>
		{/if}
	{/if}
</div>
