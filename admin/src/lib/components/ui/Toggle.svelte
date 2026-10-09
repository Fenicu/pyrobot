<script lang="ts">
	interface Props {
		checked: boolean;
		label: string;
		disabled?: boolean;
		onchange: (v: boolean) => void;
	}
	let { checked, label, disabled = false, onchange }: Props = $props();
</script>

<button
	type="button"
	role="switch"
	aria-checked={checked}
	aria-label={label}
	title={label}
	{disabled}
	onclick={() => onchange(!checked)}
	class="relative inline-flex h-[17px] w-[30px] shrink-0 items-center rounded-full transition-colors before:absolute before:-inset-2 disabled:cursor-not-allowed disabled:opacity-50 {checked
		? 'bg-accent'
		: 'bg-fg-faint/50'}"
>
	<span
		class="absolute top-[2px] size-[13px] rounded-full bg-white shadow-sm transition-[left] {checked
			? 'left-[15px]'
			: 'left-[2px]'}"
		aria-hidden="true"
	></span>
</button>
