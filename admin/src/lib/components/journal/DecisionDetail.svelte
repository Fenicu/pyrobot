<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { DecisionOut } from '$lib/api/types';
	import type { LiveEvent } from '$lib/live/sse';
	import { verdictText } from '$lib/plan/text';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment } from '$lib/util/format';
	import { pretty } from '$lib/util/text';
	import Pill from '../Pill.svelte';
	import KV from './KV.svelte';
	import RunSteps from './RunSteps.svelte';

	interface Candidate {
		scenario?: string;
		params?: Record<string, unknown>;
		score?: number | null;
		verdict?: string;
	}
	interface Props {
		api: AccountApi;
		id: number;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
	}
	let { api, id, subscribe }: Props = $props();
	let decision = $state<DecisionOut | null>(null);
	let error = $state('');
	const now = $derived(clock.now);

	$effect(() => {
		const current = id;
		decision = null;
		error = '';
		call(api.GET('/decisions/{decision_id}', { params: { path: { decision_id: current } } }))
			.then((d) => {
				if (current === id) decision = d;
			})
			.catch((e: unknown) => {
				if (current === id) error = e instanceof ApiFailure ? e.message : String(e);
			});
	});

	const candidates = $derived((decision?.candidates ?? []) as Candidate[]);

	/** Вердикт: выбранный и подходящий — зелёные, отказ — приглушён (как в макете). */
	const verdictTone = (v: string | undefined) => (v === 'chosen' || v === 'ok' ? 'ok' : 'muted');

	function paramsText(params: Record<string, unknown> | undefined): string {
		return Object.entries(params ?? {})
			.map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
			.join(', ');
	}
</script>

{#if error}
	<p class="ext-text text-sm text-bad-fg">{error}</p>
{:else if !decision}
	<p class="text-sm text-fg-muted">Решение #{id}…</p>
{:else}
	<h3 class="mb-1 font-semibold">Решение #{decision.id} · {fmtMoment(decision.at, now, true)}</h3>
	<dl>
		<KV label="Вид"><Pill tone="dec">{decision.kind === 'act' ? 'действие' : 'ожидание'}</Pill></KV>
		{#if decision.scenario}<KV label="Сценарий">{decision.scenario}</KV>{/if}
		<KV label="Причина"><span class="ext-text">{decision.reason}</span></KV>
		{#if decision.until}<KV label="До">{fmtMoment(decision.until, now)}</KV>{/if}
	</dl>
	{#if Object.keys(decision.params).length > 0}
		<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">Параметры</h4>
		<pre class="ext-text rounded bg-bg p-2 font-mono text-xs">{pretty(decision.params)}</pre>
	{/if}
	<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">Кандидаты</h4>
	{#if candidates.length === 0}
		<p class="text-sm text-fg-faint">Нет (ожидание без выбора).</p>
	{:else}
		<table class="w-full text-xs">
			<thead class="text-left text-fg-muted">
				<tr><th class="py-1 font-normal">сценарий</th><th class="font-normal">оценка</th><th class="font-normal">вердикт</th></tr>
			</thead>
			<tbody>
				{#each candidates as c, i (i)}
					{@const params = paramsText(c.params)}
					<tr class="border-t border-line-soft {c.verdict === 'chosen' ? 'font-medium' : ''}">
						<td class="py-1">
							{c.scenario ?? '—'}
							{#if params}<span class="ext-text block font-mono text-[11px] text-fg-faint">{params}</span>{/if}
						</td>
						<td class="tabular-nums">{typeof c.score === 'number' ? c.score.toFixed(2) : '—'}</td>
						<td>
							{#if c.verdict}<Pill tone={verdictTone(c.verdict)} title={c.verdict}>{verdictText(c.verdict)}</Pill>{:else}—{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
	{#if decision.runs.length > 0}
		<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">Запуск сценария</h4>
		<div class="space-y-2">
			{#each decision.runs as r (r.id)}<RunSteps {api} runId={r.id} {subscribe} />{/each}
		</div>
	{/if}
{/if}
