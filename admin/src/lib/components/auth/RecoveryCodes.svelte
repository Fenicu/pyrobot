<script lang="ts">
	import { onDestroy } from 'svelte';

	interface Props {
		codes: string[];
	}
	let { codes }: Props = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | null = null;

	async function copy() {
		try {
			await navigator.clipboard.writeText(codes.join('\n'));
			copied = true;
			if (timer !== null) clearTimeout(timer);
			timer = setTimeout(() => {
				copied = false;
				timer = null;
			}, 2000);
		} catch {
			// буфер обмена может быть недоступен в небезопасном контексте
		}
	}

	onDestroy(() => {
		if (timer !== null) {
			clearTimeout(timer);
			timer = null;
		}
	});

	function download() {
		const blob = new Blob([codes.join('\n') + '\n'], { type: 'text/plain;charset=utf-8' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'pyrobot-recovery-codes.txt';
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}
</script>

<div class="space-y-3">
	<div class="grid grid-cols-1 gap-1.5 rounded-md border border-line bg-surface-2 p-3 font-mono text-sm sm:grid-cols-2">
		{#each codes as code}
			<span class="font-mono text-fg select-all">{code}</span>
		{/each}
	</div>
	<div class="flex flex-wrap gap-2">
		<button type="button" class="btn" onclick={copy}>
			{copied ? 'Скопировано!' : 'Скопировать'}
		</button>
		<button type="button" class="btn" onclick={download}>
			Скачать .txt
		</button>
	</div>
</div>
