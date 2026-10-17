<script lang="ts">
	import { untrack } from 'svelte';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure, errorText } from '$lib/api/errors';
	import type { SettingsBulkIn } from '$lib/api/types';
	import { editableLeaves, initialValue, pathToChanges, searchLeaves, type LeafOption } from '$lib/overview/bulk';
	import type { OverviewRow } from '$lib/overview/table';
	import schemaJson from '$lib/settings/settings.schema.json';
	import { getAt, type JsonSchema } from '$lib/settings/schema';
	import { fmtValue } from '$lib/settings/value';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { accountTitle } from '$lib/util/game';
	import Modal from '../Modal.svelte';
	import Widget from '../settings/Widget.svelte';

	interface Props {
		api: Api;
		rows: OverviewRow[];
		/** Аккаунты, отмеченные в шаге «У кого» сначала (выбранные в сводке; нет выбора - все). */
		initial: number[];
		/** Тело запроса готово: страница отправляет его и показывает итоги. */
		onapply: (body: SettingsBulkIn) => Promise<void>;
		onclose: () => void;
	}
	let { api, rows, initial, onapply, onclose }: Props = $props();

	const schema = schemaJson as unknown as JsonSchema;
	const options = editableLeaves(schema);
	const targets = $derived(rows.filter((r) => r.account.status !== 'deleting'));

	let query = $state('');
	let leaf = $state<LeafOption | null>(null);
	let value = $state<unknown>(undefined);
	let source = $state('');
	let sourceError = $state<string | null>(null);
	// Начальный набор берётся один раз: дальше его меняет только пользователь.
	let picked = $state<Set<number>>(untrack(() => new Set(initial)));
	let busy = $state(false);
	let failure = $state<string | null>(null);

	const found = $derived(searchLeaves(options, query));
	const shown = $derived(found.slice(0, 40));

	function choose(option: LeafOption) {
		leaf = option;
		value = initialValue(schema, option.field);
		source = '';
		sourceError = null;
		failure = null;
	}

	async function takeFrom(id: number) {
		source = String(id);
		sourceError = null;
		if (!leaf || !id) return;
		const path = leaf.field.path;
		try {
			const out = await call(
				api.GET('/api/v1/accounts/{account_id}/settings', { params: { path: { account_id: id } } })
			);
			const taken = getAt(out.values, path);
			if (taken === undefined) sourceError = 'У аккаунта нет такого значения.';
			else value = taken;
		} catch (e) {
			sourceError = e instanceof ApiFailure ? errorText(e.error) : String(e);
		}
	}

	function toggle(id: number) {
		const next = new Set(picked);
		if (!next.delete(id)) next.add(id);
		picked = next;
	}

	const note = (row: OverviewRow) =>
		row.extra?.engine === 'stopped'
			? 'изменится в базе, применится при старте'
			: row.extra?.engine === 'elsewhere'
				? 'чужой хост'
				: null;

	const ready = $derived(leaf !== null && picked.size > 0 && !busy);

	async function apply() {
		if (!leaf || picked.size === 0) return;
		const changes = pathToChanges(leaf.path, value);
		const live = leaf.path === 'engine.mode' && value === 'live';
		if (live) {
			const ok = await dialogs.confirm({
				title: 'Включить LIVE?',
				body: `Бот начнёт реально тратить ресурсы на аккаунтах: ${picked.size}.`,
				confirmText: 'Включить live',
				danger: true
			});
			if (!ok) return;
		}
		busy = true;
		failure = null;
		try {
			await onapply({
				account_ids: targets.filter((r) => picked.has(r.account.id)).map((r) => r.account.id),
				changes,
				confirm_live: live
			});
		} catch (e) {
			failure = e instanceof ApiFailure ? errorText(e.error) : String(e);
			busy = false;
		}
	}
</script>

<Modal title="Настройка у нескольких аккаунтов" {onclose} variant="sheet">
	<div class="space-y-4 text-sm">
		<section aria-labelledby="bulk-what">
			<h3 id="bulk-what" class="font-medium">Что менять</h3>
			<input
				type="search"
				class="input mt-1 w-full"
				placeholder="Поиск по названию настройки"
				aria-label="Поиск настройки"
				bind:value={query}
				data-autofocus
			/>
			<ul class="mt-1 max-h-48 overflow-y-auto rounded-md border border-line-soft" aria-label="Настройки">
				{#each shown as option (option.path)}
					<li>
						<button
							type="button"
							class="block w-full px-2 py-1 text-left hover:bg-surface-2 {leaf?.path === option.path ? 'bg-accent-soft text-accent' : ''}"
							aria-pressed={leaf?.path === option.path}
							onclick={() => choose(option)}
						>
							<span class="ext-text">{option.label}</span>
							<span class="block text-xs text-fg-muted">{option.section} · {option.path}</span>
						</button>
					</li>
				{:else}
					<li class="px-2 py-1 text-fg-muted">Ничего не найдено.</li>
				{/each}
			</ul>
			{#if found.length > shown.length}
				<p class="mt-1 text-xs text-fg-muted">Показаны первые {shown.length} из {found.length}: уточните поиск.</p>
			{/if}
		</section>

		{#if leaf}
			<section aria-labelledby="bulk-value">
				<h3 id="bulk-value" class="font-medium">Значение</h3>
				<p class="text-xs text-fg-muted">{leaf.label}</p>
				<div class="mt-1">
					<Widget
						kind={leaf.field.type}
						{value}
						id="bulk-value-field"
						label={leaf.label}
						fallback={initialValue(schema, leaf.field)}
						onchange={(next) => (value = next)}
					/>
				</div>
				<label class="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
					Взять у аккаунта
					<select
						class="input"
						value={source}
						onchange={(e) => void takeFrom(Number((e.currentTarget as HTMLSelectElement).value))}
					>
						<option value="">-</option>
						{#each targets as row (row.account.id)}
							<option value={row.account.id}>{accountTitle(row.account)}</option>
						{/each}
					</select>
				</label>
				{#if sourceError}<p class="mt-1 text-xs text-bad-fg" role="alert">{sourceError}</p>{/if}
				<p class="mt-1 text-xs text-fg-faint">Будет записано: {fmtValue(value)}</p>
			</section>
		{/if}

		<section aria-labelledby="bulk-who">
			<h3 id="bulk-who" class="font-medium">У кого</h3>
			<ul class="mt-1 space-y-1">
				{#each targets as row (row.account.id)}
					<li>
						<label class="flex items-start gap-2">
							<input type="checkbox" class="mt-0.5" checked={picked.has(row.account.id)} onchange={() => toggle(row.account.id)} />
							<span class="ext-text">
								{accountTitle(row.account)}
								{#if note(row)}<span class="block text-xs text-warn-fg">{note(row)}</span>{/if}
							</span>
						</label>
					</li>
				{/each}
			</ul>
		</section>

		{#if failure}<p class="text-bad-fg" role="alert">{failure}</p>{/if}
	</div>
	{#snippet footer()}
		<button type="button" class="btn" onclick={onclose}>Отмена</button>
		<button type="button" class="btn btn-primary" disabled={!ready} onclick={() => void apply()}>Применить</button>
	{/snippet}
</Modal>
