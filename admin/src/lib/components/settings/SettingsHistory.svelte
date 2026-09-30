<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { SettingsVersion } from '$lib/api/types';
	import type { SettingsEditor } from '$lib/settings/editor.svelte';
	import { settingNames } from '$lib/settings/names';
	import { settingPaths } from '$lib/settings/paths.svelte';
	import { same } from '$lib/settings/schema';
	import { fmtValue } from '$lib/settings/value';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		api: AccountApi;
		/** Подписи настроек и «вернуть»: прежнее значение — в черновик, сохранение — как обычно. */
		editor: SettingsEditor;
		/** Меняется после сохранения или чужого изменения — перечитать. */
		refresh?: number;
		now?: Date;
	}
	let { api, editor, refresh = 0, now: fixedNow }: Props = $props();
	const nameOf = $derived(settingNames(editor.sections));
	const named = (path: string) => {
		const n = nameOf(path);
		return n.section ? `${n.section} · ${n.label}` : n.label;
	};
	// Вернуть можно поле формы (не «только чтение»), если в черновике сейчас другое значение.
	const revertable = (path: string, before: unknown) => {
		const field = nameOf(path).field;
		return field !== null && !field.readOnly && !same(editor.value(field.path), before);
	};
	function revert(path: string, before: unknown) {
		const field = nameOf(path).field;
		if (field) editor.set(field.path, JSON.parse(JSON.stringify(before ?? null)));
	}
	const now = $derived(fixedNow ?? clock.now);
	let items = $state<SettingsVersion[]>([]);
	let next = $state<number | null>(null);
	let open = $state<number | null>(null);
	let error = $state('');

	async function load(before: number | null) {
		try {
			const page = await call(
				api.GET('/settings/history', { params: { query: { limit: 20, ...(before ? { before } : {}) } } })
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
			.map(([path, [, after]]) => `${named(path)} → ${fmtValue(after)}`)
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
								<dt class="text-fg-muted">
									{named(path)}{#if settingPaths.show}
										<span class="block font-mono">{path}</span>{/if}
								</dt>
								<dd class="ext-text">
									<span class="text-bad-fg line-through">{fmtValue(before)}</span> →
									<span class="text-ok-fg">{fmtValue(after)}</span>
									{#if revertable(path, before)}
										<button
											type="button"
											class="ml-1 rounded px-1 text-accent hover:bg-surface-2"
											aria-label="Вернуть «{named(path)}»: {fmtValue(before)}"
											onclick={() => revert(path, before)}>вернуть</button
										>
									{/if}
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
