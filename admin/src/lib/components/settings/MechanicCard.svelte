<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { SettingsEditor } from '$lib/settings/editor.svelte';
	import { cardAbout, firstSentence, type MechanicCard } from '$lib/settings/mechanics';
	import { settingPaths } from '$lib/settings/paths.svelte';
	import { pathKey, type Field } from '$lib/settings/schema';
	import Toggle from '../ui/Toggle.svelte';
	import SettingField from './SettingField.svelte';

	interface Props {
		editor: SettingsEditor;
		card: MechanicCard;
		/** Флаг механики из схемы сервера; null — включателя нет (у карточки или у старого сервера). */
		feature: Field | null;
		/** Поля карточки (в поиске — только найденные). */
		fields: Field[];
		/** Блок после поля (обмен мандаринами — после «Сообщения для /gt»). */
		after?: Snippet<[string]>;
	}
	let { editor, card, feature, fields, after }: Props = $props();
	const titleId = $props.id();
	const about = $derived(cardAbout(card));
	const short = $derived(about ? firstSentence(about) : undefined);
	let more = $state(false);
	const on = $derived(feature ? editor.value(feature.path) === true : false);
	const toggled = $derived(feature ? editor.isChanged(feature.path) : false);
	const notDefault = $derived(
		fields.filter((f) => editor.defaultValue(f.path) !== undefined && !editor.isDefault(f.path)).length
	);
</script>

<section class="card @container min-w-0" aria-labelledby={titleId} data-card={card.id}>
	<div class="flex items-center gap-2.5">
		{#if feature}
			<span class="inline-flex rounded-full {toggled ? 'ring-1 ring-accent ring-offset-2 ring-offset-surface' : ''}">
				<Toggle
					checked={on}
					label={card.title}
					describedby={feature.unused ? `${titleId}-unused` : undefined}
					onchange={(v) => editor.set(feature.path, v)}
				/>
			</span>
		{/if}
		<h3 id={titleId} class="min-w-0 text-[13px] font-semibold {feature?.unused ? 'text-fg-muted' : ''}">
			<span aria-hidden="true">{card.icon}</span>
			{card.title}
		</h3>
		{#if notDefault > 0}
			<span class="ml-auto shrink-0 text-[11px] text-fg-muted">{notDefault} не по умолчанию</span>
		{/if}
	</div>
	{#if feature && settingPaths.show}<p class="font-mono text-[11px] text-fg-faint">{pathKey(feature.path)}</p>{/if}
	{#if feature?.unused}<p id="{titleId}-unused" class="text-xs text-fg-muted">не используется ботом</p>{/if}
	{#if about}
		<p class="ext-text mt-1 mb-1 text-xs text-fg-muted">
			{more ? about : short}
			{#if about !== short}
				<button type="button" class="text-accent hover:underline" aria-expanded={more} onclick={() => (more = !more)}
					>{more ? 'Свернуть' : 'Подробнее'}</button
				>
			{/if}
		</p>
	{/if}
	{#each fields as f (pathKey(f.path))}
		<SettingField {editor} field={f} />
		{@render after?.(pathKey(f.path))}
	{/each}
</section>
