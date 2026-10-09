<script lang="ts">
	import { boundsText, type SettingsEditor } from '$lib/settings/editor.svelte';
	import { settingHelp, settingLabel, valueLabels } from '$lib/settings/labels';
	import { firstSentence } from '$lib/settings/mechanics';
	import { settingPaths } from '$lib/settings/paths.svelte';
	import { pathKey, type Field, type FieldKind } from '$lib/settings/schema';
	import { fmtValue } from '$lib/settings/value';
	import Self from './SettingField.svelte';
	import Widget from './Widget.svelte';

	interface Props {
		editor: SettingsEditor;
		field: Field;
	}
	let { editor, field }: Props = $props();
	const key = $derived(pathKey(field.path));
	const label = $derived(settingLabel(key, field.title));
	const help = $derived(settingHelp(key) ?? field.description);
	// Под подписью — первое предложение описания, если оно короткое; ⓘ раскрывает описание целиком.
	const SHORT_HINT = 120;
	const hint = $derived.by(() => {
		const first = help ? firstSentence(help) : undefined;
		return first !== undefined && first.length <= SHORT_HINT ? first : undefined;
	});
	const id = $derived(`set-${key.replaceAll('.', '-')}`);
	const bound = $derived(editor.bound(field.path));
	const boundHint = $derived(bound === null ? null : boundsText(bound));
	const describedby = $derived(
		[
			field.unused ? `${id}-unused` : null,
			help ? `${id}-help` : null,
			boundHint ? `${id}-bound` : null
		]
			.filter(Boolean)
			.join(' ') || undefined
	);
	let open = $state(false);
	const value = $derived(editor.value(field.path));
	const changed = $derived(editor.isChanged(field.path));
	const fallback = $derived(editor.defaultValue(field.path));
	// Нет умолчания (сервер его не прислал) — нечего и сбрасывать.
	const notDefault = $derived(fallback !== undefined && !editor.isDefault(field.path));
	const error = $derived(editor.fieldErrors[key]);
	// Списки и словари — под подписью всегда; составные поля — под подписью на телефоне; остальные — справа.
	const WIDE = ['enum_tags', 'string_tags', 'map', 'json', 'group'];
	const NARROW = ['boolean', 'number', 'enum', 'const'];
	const wide = (k: FieldKind): boolean => WIDE.includes(k.kind) || (k.kind === 'nullable' && wide(k.inner));
	const span = $derived(
		wide(field.type) ? 'col-span-2' : NARROW.includes(field.type.kind) ? '' : 'max-sm:col-span-2'
	);
	// В широкой карточке (одна колонка) подпись не шире 24rem: значение остаётся рядом с ней, а не у края.
	const capped = $derived(wide(field.type) ? '' : '@xl:grid-cols-[minmax(0,24rem)_auto] @xl:justify-start');
</script>

{#if field.type.kind === 'group'}
	<fieldset class="mt-3 rounded-md border border-line-soft p-2">
		<legend class="px-1 text-xs font-semibold text-fg-muted uppercase">
			{label}{#if settingPaths.show}
				<span class="font-mono font-normal normal-case">({key})</span>{/if}
		</legend>
		{#if help}<p class="mb-1 text-[11px] leading-snug text-fg-muted">{help}</p>{/if}
		{#each field.type.fields as child (pathKey(child.path))}
			<Self {editor} field={child} />
		{/each}
	</fieldset>
{:else}
	<div class="group/field relative grid grid-cols-[minmax(0,1fr)_auto] {capped} items-center gap-x-3 gap-y-0.5 border-t border-line-soft py-1.5" data-path={key}>
		<div class="min-w-0 {span}">
			<div class="flex items-start gap-1">
				<label for={id} class="min-w-0 text-sm {field.unused ? 'text-fg-muted' : ''}">
					{label}
					{#if settingPaths.show}<span class="block font-mono text-[11px] text-fg-faint">{key}</span>{/if}
				</label>
				{#if help && help !== hint}
					<button
						type="button"
						class="-my-0.5 rounded-full px-1 text-xs leading-none text-fg-faint hover:bg-surface-2 hover:text-fg"
						aria-expanded={open}
						aria-controls="{id}-help"
						aria-label="Описание: {label}"
						onclick={() => (open = !open)}>ⓘ</button
					>
				{/if}
			</div>
			{#if field.unused}<p id="{id}-unused" class="text-xs text-fg-muted">не используется ботом</p>{/if}
			{#if help}
				<!-- Свёрнутое длинное описание скрыто, но целиком: на него ссылается aria-describedby. -->
				<p id="{id}-help" class="ext-text text-[11px] leading-snug text-fg-muted {open || hint ? '' : 'hidden'}">
					{open || !hint ? help : hint}
				</p>
			{/if}
		</div>
		<div class="flex min-w-0 items-center gap-1.5 {span} {wide(field.type) ? '' : 'sm:justify-end'}">
			{#if notDefault}
				<span class="size-1.5 shrink-0 rounded-full bg-accent {capped ? '@xl:-ml-3' : ''}" title="Отличается от умолчания"></span>
			{/if}
			<div class="min-w-0 rounded-md {changed ? 'ring-1 ring-accent ring-offset-2 ring-offset-surface' : ''} {wide(field.type) ? 'flex-1' : ''}">
				{#if field.readOnly}
					<span class="ext-text text-sm" {id} aria-describedby={describedby}>{fmtValue(value)}</span>
					<span class="ml-2 text-xs whitespace-nowrap text-fg-faint">только чтение</span>
				{:else}
					<Widget
						kind={field.type}
						{value}
						{id}
						{label}
						labels={valueLabels(key)}
						{describedby}
						{fallback}
						invalid={!!error}
						onchange={(next) => editor.set(field.path, next)}
					/>
				{/if}
			</div>
		</div>
		{#if boundHint}<p id="{id}-bound" class="col-span-2 text-right text-[11px] text-fg-faint">{boundHint}</p>{/if}
		{#if error}<p class="ext-text col-span-2 text-xs text-bad-fg" role="alert">{error}</p>{/if}
		{#if notDefault && !field.readOnly}
			<!-- На телефоне — строкой под полем; на ПК — плашкой под точкой по наведению или фокусу в строке
			     (место под неё не резервируется). -->
			<button
				type="button"
				class="ext-text col-span-2 justify-self-start text-left text-[11px] text-accent hover:underline md:invisible md:absolute md:top-full md:right-0 md:z-10 {capped ? '@xl:right-auto @xl:left-[24.75rem]' : ''} md:max-w-80 md:-translate-y-1 md:rounded-md md:border md:border-line md:bg-surface-2 md:px-2 md:py-0.5 md:shadow-lg md:group-focus-within/field:visible md:group-hover/field:visible"
				onclick={() => editor.resetToDefault(field.path)}>сбросить к умолчанию ({fmtValue(fallback)})</button
			>
		{/if}
	</div>
{/if}
