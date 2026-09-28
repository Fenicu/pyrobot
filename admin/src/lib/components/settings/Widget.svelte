<script lang="ts">
	import type { FieldKind } from '$lib/settings/schema';
	import TagsInput from './TagsInput.svelte';
	import Self from './Widget.svelte';

	interface Props {
		kind: FieldKind;
		value: unknown;
		id: string;
		label: string;
		/** Значение, которое ставится при включении пустого (null) поля. */
		fallback?: unknown;
		invalid?: boolean;
		/** id описания поля (`SettingField`, `{id}-help`): связывает поле ввода с ним на ПК. */
		describedby?: string;
		onchange: (next: unknown) => void;
	}
	let { kind, value, id, label, fallback, invalid = false, describedby, onchange }: Props = $props();
	let mapKey = $state('');
	let jsonText = $state('');
	let jsonError = $state('');

	$effect(() => {
		if (kind.kind === 'json') jsonText = JSON.stringify(value, null, 1);
	});

	function num(raw: string): unknown {
		if (raw.trim() === '') return raw;
		const n = Number(raw);
		return Number.isFinite(n) ? n : raw;
	}

	function blank(k: FieldKind): unknown {
		if (fallback !== null && fallback !== undefined) return fallback;
		switch (k.kind) {
			case 'number':
			case 'max_or_int':
				return k.kind === 'number' ? (k.min ?? 0) : 0;
			case 'boolean':
				return false;
			case 'enum':
				return k.options[0] ?? '';
			case 'enum_tags':
			case 'string_tags':
				return [];
			default:
				return '';
		}
	}
</script>

{#if kind.kind === 'boolean'}
	<label class="inline-flex cursor-pointer items-center gap-2">
		<input
			{id}
			type="checkbox"
			role="switch"
			class="peer sr-only"
			checked={value === true}
			aria-label={label}
			aria-describedby={describedby}
			onchange={(e) => onchange(e.currentTarget.checked)}
		/>
		<span
			class="relative h-5 w-9 rounded-full bg-muted-bg transition-colors peer-checked:bg-accent peer-focus-visible:outline-2 peer-focus-visible:outline-accent after:absolute after:top-0.5 after:left-0.5 after:size-4 after:rounded-full after:bg-white after:transition-transform peer-checked:after:translate-x-4"
			aria-hidden="true"
		></span>
		<span class="text-xs text-fg-muted">{value === true ? 'вкл' : 'выкл'}</span>
	</label>
{:else if kind.kind === 'enum'}
	<select {id} class="input" aria-label={label} aria-invalid={invalid || undefined} aria-describedby={describedby} value={String(value ?? '')} onchange={(e) => onchange(e.currentTarget.value)}>
		{#each kind.options as o (o)}<option value={o}>{o}</option>{/each}
	</select>
{:else if kind.kind === 'const'}
	<span class="font-mono text-sm">{String(kind.value)}</span>
{:else if kind.kind === 'number'}
	<input
		{id}
		class="input max-w-40"
		type="number"
		inputmode={kind.integer ? 'numeric' : 'decimal'}
		step={kind.integer ? 1 : 'any'}
		min={kind.min}
		max={kind.max}
		aria-label={label}
		aria-invalid={invalid || undefined}
		aria-describedby={describedby}
		value={value === null || value === undefined ? '' : String(value)}
		oninput={(e) => onchange(num(e.currentTarget.value))}
	/>
{:else if kind.kind === 'string'}
	<input {id} class="input" aria-label={label} aria-invalid={invalid || undefined} aria-describedby={describedby} value={String(value ?? '')} oninput={(e) => onchange(e.currentTarget.value)} />
{:else if kind.kind === 'enum_tags'}
	<TagsInput value={(value as string[]) ?? []} options={kind.options} {label} onchange={onchange} />
{:else if kind.kind === 'string_tags'}
	<TagsInput value={(value as string[]) ?? []} {label} onchange={onchange} />
{:else if kind.kind === 'max_or_int'}
	<span class="inline-flex items-center gap-1">
		<select
			{id}
			class="input w-auto"
			aria-label="{label}: вид"
			aria-describedby={describedby}
			value={value === 'max' ? 'max' : 'num'}
			onchange={(e) => onchange(e.currentTarget.value === 'max' ? 'max' : (kind.min ?? 0))}
		>
			<option value="max">max</option>
			<option value="num">число</option>
		</select>
		{#if value !== 'max'}
			<input
				class="input w-24"
				type="number"
				step="1"
				min={kind.min}
				aria-label="{label}: число"
				aria-invalid={invalid || undefined}
				aria-describedby={describedby}
				value={String(value ?? '')}
				oninput={(e) => onchange(num(e.currentTarget.value))}
			/>
		{/if}
	</span>
{:else if kind.kind === 'nullable'}
	<span class="inline-flex flex-wrap items-center gap-2">
		<label class="inline-flex items-center gap-1 text-xs text-fg-muted">
			<input
				type="checkbox"
				checked={value !== null && value !== undefined}
				aria-label="{label}: задано"
				onchange={(e) => onchange(e.currentTarget.checked ? blank(kind.inner) : null)}
			/>
			задано
		</label>
		{#if value !== null && value !== undefined}
			<Self kind={kind.inner} {value} {id} {label} {invalid} {describedby} {onchange} />
		{/if}
	</span>
{:else if kind.kind === 'map'}
	{@const entries = Object.entries((value as Record<string, unknown>) ?? {})}
	<div class="space-y-1" role="group" aria-label={label}>
		{#each entries as [k, v] (k)}
			<div class="flex items-center gap-1">
				<span class="w-16 font-mono text-xs">{k}</span>
				<Self
					kind={kind.value}
					value={v}
					id="{id}-{k}"
					label="{label}: {k}"
					onchange={(next) => onchange({ ...(value as object), [k]: next })}
				/>
				<button
					type="button"
					class="btn btn-ghost min-h-7 px-2"
					aria-label="Убрать {k}"
					onclick={() => {
						const { [k]: _, ...rest } = value as Record<string, unknown>;
						onchange(rest);
					}}>✕</button
				>
			</div>
		{/each}
		<div class="flex items-center gap-1">
			<input class="input w-24" placeholder="ключ" aria-label="{label}: новый ключ" bind:value={mapKey} />
			<button
				type="button"
				class="btn min-h-8"
				disabled={mapKey.trim() === '' || entries.some(([k]) => k === mapKey.trim())}
				onclick={() => {
					onchange({ ...((value as object) ?? {}), [mapKey.trim()]: blank(kind.value) });
					mapKey = '';
				}}>Добавить</button
			>
		</div>
	</div>
{:else}
	<textarea
		{id}
		class="input min-h-20 font-mono text-xs"
		aria-label={label}
		aria-describedby={describedby}
		bind:value={jsonText}
		onchange={() => {
			try {
				onchange(JSON.parse(jsonText));
				jsonError = '';
			} catch {
				jsonError = 'не JSON';
			}
		}}
	></textarea>
	{#if jsonError}<span class="text-xs text-bad-fg">{jsonError}</span>{/if}
{/if}
