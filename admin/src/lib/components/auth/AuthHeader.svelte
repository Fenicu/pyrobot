<script lang="ts">
	interface Props {
		title?: string;
		subtitle?: string;
	}
	let { title = 'pyrobot', subtitle }: Props = $props();
</script>

<div class="flex items-start gap-3">
	<span
		class="grid size-9 shrink-0 place-items-center rounded-ctl bg-accent-soft text-base font-bold text-accent"
		aria-hidden="true">p</span
	>
	<div class="min-w-0">
		<h1 class="text-base leading-tight font-semibold">{title}</h1>
		{#if subtitle}<p class="text-[13px] text-fg-muted">{subtitle}</p>{/if}
	</div>
</div>
