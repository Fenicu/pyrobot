<script lang="ts">
	import type { SettingsEditor } from '$lib/settings/editor.svelte';
	import { FIELD_LABELS } from '$lib/settings/labels';
	import { pathKey, type Field } from '$lib/settings/schema';
	import { fmtValue } from '$lib/settings/value';
	import Pill from '../Pill.svelte';
	import Self from './SettingField.svelte';
	import Widget from './Widget.svelte';

	interface Props {
		editor: SettingsEditor;
		field: Field;
	}
	let { editor, field }: Props = $props();
	const key = $derived(pathKey(field.path));
	const label = $derived(FIELD_LABELS[key] ?? field.title);
	const id = $derived(`set-${key.replaceAll('.', '-')}`);
	const value = $derived(editor.value(field.path));
	const changed = $derived(editor.isChanged(field.path));
	const notDefault = $derived(!editor.isDefault(field.path));
	const error = $derived(editor.fieldErrors[key]);
</script>

{#if field.type.kind === 'group'}
	<fieldset class="mt-3 rounded-md border border-line-soft p-2">
		<legend class="px-1 text-xs font-semibold text-fg-muted uppercase">
			{label} <span class="font-mono font-normal normal-case">({key})</span>
		</legend>
		{#if field.description}<p class="mb-1 text-xs text-fg-faint">{field.description}</p>{/if}
		{#each field.type.fields as child (pathKey(child.path))}
			<Self {editor} field={child} />
		{/each}
	</fieldset>
{:else}
	<div
		class="grid gap-1 border-b border-line-soft py-2 md:grid-cols-[16rem_minmax(0,1fr)_9rem] md:items-center md:gap-3"
		data-path={key}
	>
		<label for={id} class="text-sm">
			{label}
			<span class="block font-mono text-[11px] text-fg-faint">{key}</span>
		</label>
		<div class="min-w-0 rounded-md {changed ? 'ring-1 ring-accent ring-offset-2 ring-offset-surface' : ''}">
			{#if field.readOnly}
				<span class="ext-text text-sm" {id}>{fmtValue(value)}</span>
				<span class="ml-2 text-xs text-fg-faint">только чтение</span>
			{:else}
				<Widget
					kind={field.type}
					{value}
					{id}
					{label}
					fallback={editor.defaultValue(field.path)}
					invalid={!!error}
					onchange={(next) => editor.set(field.path, next)}
				/>
			{/if}
			{#if error}<p class="ext-text mt-1 text-xs text-bad-fg" role="alert">{error}</p>{/if}
		</div>
		<div class="flex flex-wrap items-center gap-1 text-xs text-fg-faint">
			{#if changed}
				<Pill tone="dec">изменено</Pill>
			{:else if notDefault}
				<span title="Значение отличается от умолчания" class="text-accent">● не по умолч.</span>
			{/if}
			<span class="ext-text">умолч.: {fmtValue(editor.defaultValue(field.path))}</span>
		</div>
	</div>
{/if}
