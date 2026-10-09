<script lang="ts">
	interface Props {
		tone: 'ok' | 'warn' | 'bad' | 'off';
		label: string;
	}
	let { tone, label }: Props = $props();

	const COLOR = { ok: 'bg-ok', warn: 'bg-warn', bad: 'bg-bad', off: 'bg-off' } as const;
</script>

<span class="inline-block size-[7px] shrink-0 rounded-full {COLOR[tone]}" role="img" aria-label={label} title={label}
></span>
