<script lang="ts">
	interface Props {
		value: number;
		max: number;
		label: string;
	}
	let { value, max, label }: Props = $props();
	const pct = $derived(max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0);
</script>

<div
	class="my-1 h-1.5 overflow-hidden rounded-full bg-surface-2"
	role="progressbar"
	aria-label={label}
	aria-valuemin="0"
	aria-valuemax={max}
	aria-valuenow={value}
>
	<div class="h-full rounded-full bg-accent" style:width="{pct}%"></div>
</div>
