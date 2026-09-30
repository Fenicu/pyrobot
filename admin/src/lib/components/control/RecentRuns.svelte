<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { ScenarioRunOut } from '$lib/api/types';
	import type { LiveEvent } from '$lib/live/sse';
	import { fmtMoment } from '$lib/util/format';
	import { statusTone } from '$lib/util/game';
	import RunSteps from '../journal/RunSteps.svelte';
	import Pill from '../Pill.svelte';

	interface Props {
		api: AccountApi;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		/** Меняется снаружи после нового запуска — список перечитывается. */
		refresh?: number;
		now?: Date;
	}
	let { api, subscribe, refresh = 0, now = new Date() }: Props = $props();
	let runs = $state<ScenarioRunOut[]>([]);
	let error = $state('');
	let open = $state<number | null>(null);

	async function load() {
		try {
			const page = await call(
				api.GET('/scenario-runs', { params: { query: { manual: true, limit: 10 } } })
			);
			runs = page.items;
			error = '';
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void refresh;
		void load();
	});

	// Живые статусы: кадр scenario_run меняет строку; новый запуск — перечитать список.
	$effect(() => {
		if (!subscribe) return;
		return subscribe((e) => {
			if (e.type === 'reset') return void load();
			if (e.type !== 'scenario_run') return;
			const i = runs.findIndex((r) => r.id === e.data.id);
			const run = runs[i];
			if (run) runs[i] = { ...run, status: e.data.status, reason: e.data.reason || run.reason };
			else if (e.data.status === 'queued') void load();
		});
	});
</script>

<section class="card" aria-labelledby="recent-title">
	<h2 id="recent-title" class="card-title">Последние ручные запуски</h2>
	{#if error}<p class="ext-text text-sm text-bad-fg">{error}</p>{/if}
	<ul aria-label="Ручные запуски">
		{#each runs as r (r.id)}
			<li class="border-b border-line-soft">
				<button
					type="button"
					class="flex w-full items-center justify-between gap-2 py-1.5 text-left text-sm"
					aria-expanded={open === r.id}
					onclick={() => (open = open === r.id ? null : r.id)}
				>
					<span class="min-w-0 truncate">
						<span class="font-mono text-xs text-fg-faint">{fmtMoment(r.started_at, now)}</span>
						<span class="font-mono">{r.scenario}</span>
					</span>
					<Pill tone={statusTone(r.status)}>{r.status}{r.reason ? ` · ${r.reason}` : ''}</Pill>
				</button>
				{#if open === r.id}
					<div class="pb-2"><RunSteps {api} runId={r.id} {subscribe} /></div>
				{/if}
			</li>
		{:else}
			<li class="py-1.5 text-sm text-fg-muted">Ручных запусков ещё не было.</li>
		{/each}
	</ul>
</section>
