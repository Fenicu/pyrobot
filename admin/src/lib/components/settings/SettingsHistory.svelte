<script lang="ts">
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { SettingsVersion } from '$lib/api/types';
	import { fmtValue } from '$lib/settings/value';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		api: Api;
		/** Меняется после сохранения или чужого изменения — перечитать. */
		refresh?: number;
		now?: Date;
	}
	let { api, refresh = 0, now = new Date() }: Props = $props();
	let items = $state<SettingsVersion[]>([]);
	let next = $state<number | null>(null);
	let open = $state<number | null>(null);
	let error = $state('');

	async function load(before: number | null) {
		try {
			const page = await call(
				api.GET('/api/v1/settings/history', { params: { query: { limit: 20, ...(before ? { before } : {}) } } })
			);
			items = before ? [...items, ...page.items] : page.items;
			next = page.next_before;
			error = '';
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void refresh;
		void load(null);
	});

	function summary(v: SettingsVersion): string {
		const entries = Object.entries(v.changes);
		const head = entries
			.slice(0, 2)
			.map(([path, [, after]]) => `${path} → ${fmtValue(after)}`)
			.join('; ');
		return entries.length > 2 ? `${head}; ещё ${entries.length - 2}` : head;
	}
</script>

<section aria-labelledby="history-title">
	<h2 id="history-title" class="card-title">История</h2>
	{#if error}<p class="ext-text text-sm text-bad-fg">{error}</p>{/if}
	<ul class="space-y-0.5" aria-label="Версии настроек">
		{#each items as v (v.version)}
			<li class="border-b border-line-soft">
				<button
					type="button"
					class="w-full py-1.5 text-left text-xs"
					aria-expanded={open === v.version}
					onclick={() => (open = open === v.version ? null : v.version)}
				>
					<b>v{v.version}</b>
					<span class="text-fg-faint">{fmtMoment(v.changed_at, now)} · {v.changed_by}</span>
					<span class="ext-text block text-fg-muted">{summary(v) || 'без изменений'}</span>
				</button>
				{#if open === v.version}
					<dl class="mb-2 space-y-1 text-xs" aria-label="Изменения версии {v.version}">
						{#each Object.entries(v.changes) as [path, [before, after]] (path)}
							<div>
								<dt class="font-mono text-fg-muted">{path}</dt>
								<dd class="ext-text">
									<span class="text-bad-fg line-through">{fmtValue(before)}</span> →
									<span class="text-ok-fg">{fmtValue(after)}</span>
								</dd>
							</div>
						{/each}
					</dl>
				{/if}
			</li>
		{/each}
	</ul>
	{#if next !== null}
		<button type="button" class="btn mt-2 w-full" onclick={() => void load(next)}>Ещё</button>
	{/if}
</section>
