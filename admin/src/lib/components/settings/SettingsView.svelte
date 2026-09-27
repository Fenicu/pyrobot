<script lang="ts">
	import Search from '@lucide/svelte/icons/search';
	import type { Api } from '$lib/api/client';
	import { errorText } from '$lib/api/errors';
	import type { SettingsEditor } from '$lib/settings/editor.svelte';
	import { FIELD_LABELS, SECTION_LABELS } from '$lib/settings/labels';
	import { leaves, pathKey, type Field, type Section } from '$lib/settings/schema';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import SettingField from './SettingField.svelte';
	import SettingsHistory from './SettingsHistory.svelte';

	interface Props {
		api: Api;
		editor: SettingsEditor;
		now?: Date;
	}
	let { api, editor, now }: Props = $props();
	let active = $state<string | null>(null);
	let query = $state('');
	let historyTick = $state(0);

	const section = $derived(editor.sections.find((s) => s.name === active) ?? editor.sections[0] ?? null);
	const title = (s: Section) => SECTION_LABELS[s.name] ?? s.title;
	const q = $derived(query.trim().toLowerCase());
	const found = $derived.by(() => {
		if (!q) return [];
		const hit = (f: Field) =>
			pathKey(f.path).toLowerCase().includes(q) ||
			(FIELD_LABELS[pathKey(f.path)] ?? f.title).toLowerCase().includes(q);
		return editor.sections
			.map((s) => ({ section: s, fields: leaves(s.fields).filter(hit) }))
			.filter((r) => r.fields.length > 0);
	});
	const count = $derived(editor.changes.length);

	async function save() {
		const result = await editor.save(() =>
			dialogs.confirm({
				title: 'Включить LIVE?',
				body: 'Бот начнёт реально тратить ресурсы: команды действий уйдут в игру.',
				confirmText: 'Сохранить и включить live',
				danger: true
			})
		);
		if (result.ok) {
			toasts.show(`Сохранено, версия ${result.version}`, 'ok');
			historyTick += 1;
		} else if ('error' in result && result.error.kind !== 'version_conflict' && result.error.kind !== 'validation') {
			toasts.show(errorText(result.error), 'error');
		} else if ('error' in result && result.error.kind === 'validation') {
			toasts.show('Сервер не принял значения — поля подсвечены', 'error');
		}
	}

	async function reread() {
		await editor.load();
		historyTick += 1;
	}
</script>

{#if editor.loadError && !editor.server}
	<p class="card ext-text text-sm text-bad-fg" role="alert">{errorText(editor.loadError)}</p>
{:else if !editor.server}
	<p class="text-sm text-fg-muted" role="status">Загрузка…</p>
{:else}
	{#if editor.conflict !== null}
		<div class="card mb-3 flex flex-wrap items-center gap-2 border-warn-bg text-sm" role="alert">
			<span class="flex-1">
				Настройки уже изменены (версия {editor.conflict}, у вас — {editor.version}). Перечитайте: несохранённые
				правки пропадут.
			</span>
			<button type="button" class="btn" onclick={reread}>Перечитать</button>
		</div>
	{/if}
	{#if editor.restartRequired.length > 0}
		<div class="card mb-3 text-sm text-warn-fg" role="status">
			Сохранено, но вступит в силу после перезапуска сервиса: <span class="font-mono"
				>{editor.restartRequired.join(', ')}</span
			>
		</div>
	{/if}

	<div class="grid gap-3 lg:grid-cols-[12rem_minmax(0,1fr)_16rem]">
		<nav class="min-w-0 space-y-2" aria-label="Секции настроек">
			<label class="relative block">
				<span class="sr-only">Поиск настройки</span>
				<Search class="pointer-events-none absolute top-2.5 left-2 size-4 text-fg-faint" aria-hidden="true" />
				<input class="input pl-8" type="search" placeholder="поиск настройки…" bind:value={query} />
			</label>
			<ul class="flex gap-1 overflow-x-auto lg:flex-col">
				{#each editor.sections as s (s.name)}
					{@const dirty = editor.changes.some((p) => p[0] === s.name)}
					<li>
						<button
							type="button"
							class="w-full rounded-md px-2 py-1 text-left text-sm whitespace-nowrap hover:bg-surface-2 {section?.name ===
								s.name && !q
								? 'bg-accent-soft font-medium'
								: 'text-fg-muted'}"
							aria-current={section?.name === s.name && !q ? 'true' : undefined}
							onclick={() => {
								active = s.name;
								query = '';
							}}
						>
							{title(s)}{#if dirty}<span class="text-accent"> •</span>{/if}
						</button>
					</li>
				{/each}
			</ul>
		</nav>

		<section class="card min-w-0" aria-label={q ? 'Найденные настройки' : section ? title(section) : 'Настройки'}>
			{#if q}
				{#each found as r (r.section.name)}
					<h2 class="card-title mt-2">{title(r.section)}</h2>
					{#each r.fields as f (pathKey(f.path))}<SettingField {editor} field={f} />{/each}
				{:else}
					<p class="text-sm text-fg-muted">Ничего не найдено.</p>
				{/each}
			{:else if section}
				<h2 class="card-title">{title(section)}</h2>
				{#if section.description}<p class="mb-1 text-xs text-fg-faint">{section.description}</p>{/if}
				{#each section.fields as f (pathKey(f.path))}<SettingField {editor} field={f} />{/each}
			{/if}
		</section>

		<aside class="card">
			<SettingsHistory {api} refresh={historyTick} {now} />
		</aside>
	</div>

	{#if count > 0}
		<div
			class="sticky bottom-16 z-30 mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-accent bg-accent-soft p-2 text-sm md:bottom-2"
			role="region"
			aria-label="Несохранённые изменения"
		>
			<span class="flex-1">
				{count} {count === 1 ? 'изменение' : count < 5 ? 'изменения' : 'изменений'} · версия {editor.version}
				{#if editor.goesLive}<span class="text-warn-fg"> · включает LIVE</span>{/if}
			</span>
			<button type="button" class="btn" disabled={editor.saving} onclick={() => editor.discard()}>Отменить</button>
			<button type="button" class="btn btn-primary" disabled={editor.saving} onclick={save}>
				{editor.saving ? 'Сохранение…' : 'Сохранить'}
			</button>
		</div>
	{/if}
{/if}
