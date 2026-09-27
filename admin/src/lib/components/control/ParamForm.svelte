<script lang="ts">
	import type { ParamSpec } from '$lib/api/types';
	import { paramError, paramsOf, type ParamValues } from '$lib/params';

	interface Props {
		required: Record<string, ParamSpec>;
		busy?: boolean;
		submitLabel?: string;
		onsubmit: (params: Record<string, string | number>) => void;
		/** Параметры изменились — ключ идемпотентности прежней отправки больше не годится. */
		onedit?: () => void;
	}
	let { required, busy = false, submitLabel = 'Запустить', onsubmit, onedit }: Props = $props();
	let values = $state<ParamValues>({});
	let touched = $state(false);
	const fields = $derived(Object.entries(required));
	const errors = $derived(Object.fromEntries(fields.map(([n, s]) => [n, paramError(s, values[n] ?? '')])));
	const valid = $derived(Object.values(errors).every((e) => e === null));

	function submit(e: SubmitEvent) {
		e.preventDefault();
		touched = true;
		if (valid) onsubmit(paramsOf(required, values));
	}

	function set(name: string, value: string) {
		values = { ...values, [name]: value };
		onedit?.();
	}

	function hint(spec: ParamSpec): string {
		if (spec.type === 'int') {
			const range = [spec.min !== undefined ? `от ${spec.min}` : '', spec.max !== undefined ? `до ${spec.max}` : '']
				.filter(Boolean)
				.join(' ');
			return range ? `целое ${range}` : 'целое';
		}
		if (spec.type === 'string' && spec.pattern) return `шаблон ${spec.pattern}`;
		return '';
	}
</script>

<form class="space-y-2" onsubmit={submit} novalidate>
	{#each fields as [name, spec] (name)}
		{@const error = touched ? errors[name] : null}
		<label class="block space-y-1">
			<span class="label">{name}{hint(spec) ? ` · ${hint(spec)}` : ''}</span>
			{#if spec.type === 'enum'}
				<select
					class="input"
					value={values[name] ?? ''}
					required
					aria-invalid={error ? 'true' : undefined}
					onchange={(e) => set(name, e.currentTarget.value)}
				>
					<option value="" disabled>выберите…</option>
					{#each spec.values ?? [] as v (v)}<option value={v}>{v}</option>{/each}
				</select>
			{:else}
				<input
					class="input"
					type={spec.type === 'int' ? 'number' : 'text'}
					inputmode={spec.type === 'int' ? 'numeric' : undefined}
					step={spec.type === 'int' ? 1 : undefined}
					min={spec.min}
					max={spec.max}
					required
					autocomplete="off"
					value={values[name] ?? ''}
					aria-invalid={error ? 'true' : undefined}
					oninput={(e) => set(name, e.currentTarget.value)}
				/>
			{/if}
			{#if error}<span class="text-xs text-bad-fg" role="alert">{error}</span>{/if}
		</label>
	{/each}
	{#if fields.length === 0}
		<p class="text-xs text-fg-faint">Параметров нет: недостающее подставит планировщик.</p>
	{/if}
	<button type="submit" class="btn btn-primary" disabled={busy}>{busy ? 'Отправка…' : submitLabel}</button>
</form>
