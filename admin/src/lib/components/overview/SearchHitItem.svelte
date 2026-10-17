<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';
	import type { SearchHitOut } from '$lib/api/types';
	import { accountHref } from '$lib/nav';
	import { fmtMoment } from '$lib/util/format';

	interface Props {
		hit: SearchHitOut;
		accountName: string;
		now?: Date;
	}
	let { hit, accountName, now = new Date() }: Props = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | null = null;

	async function copy() {
		if (!hit.text) return;
		try {
			await navigator.clipboard.writeText(hit.text);
			copied = true;
			if (timer !== null) clearTimeout(timer);
			timer = setTimeout(() => {
				copied = false;
				timer = null;
			}, 2000);
		} catch {
			// Буфер обмена может быть недоступен в некоторых окружениях
		}
	}

	const inlineButtons = $derived(
		((hit.markup?.inline as unknown[]) ?? [])
			.map((b) => (Array.isArray(b) ? String(b[0] ?? '') : ''))
			.filter(Boolean)
	);

	const replyButtons = $derived(
		((hit.markup?.reply as unknown[]) ?? [])
			.flatMap((row) => (Array.isArray(row) ? row.map(String) : []))
			.filter(Boolean)
	);
</script>

<article
	data-testid="search-hit"
	class="flex flex-col gap-2 rounded-card border border-line-soft bg-surface p-3 text-sm"
>
	<header class="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft pb-1.5 text-xs">
		<div class="flex items-center gap-2">
			<a href={accountHref(hit.account_id, '')} class="font-medium hover:underline">
				{accountName}
			</a>
			<time class="text-fg-muted tabular-nums" datetime={hit.date}>
				{fmtMoment(hit.date, now)}
			</time>
			{#if hit.outgoing}
				<span class="pill pill-dec">→ игре</span>
			{:else}
				<span class="pill pill-muted">← игра</span>
			{/if}
		</div>
		<button
			type="button"
			class="btn btn-ghost shrink-0 px-2 py-0.5 text-xs text-fg-muted hover:text-fg"
			title="Копировать текст"
			aria-label="Копировать текст"
			onclick={copy}
		>
			{#if copied}
				<Check class="size-3 text-ok" aria-hidden="true" />
				<span class="text-[11px] text-ok">Скопировано</span>
			{:else}
				<Copy class="size-3" aria-hidden="true" />
				<span class="text-[11px]">Копировать</span>
			{/if}
		</button>
	</header>

	<div class="ext-text min-w-0 text-sm">
		{hit.text ?? '(без текста)'}
	</div>

	{#if inlineButtons.length > 0 || replyButtons.length > 0}
		<div class="flex flex-wrap gap-1 pt-1" role="group" aria-label="Кнопки сообщения">
			{#each inlineButtons as label, i (`in_${i}_${label}`)}
				<span class="pill pill-muted">{label}</span>
			{/each}
			{#each replyButtons as label, i (`rep_${i}_${label}`)}
				<span class="pill pill-muted">{label}</span>
			{/each}
		</div>
	{/if}
</article>
