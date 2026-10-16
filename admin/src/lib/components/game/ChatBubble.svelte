<script lang="ts">
	import Bot from '@lucide/svelte/icons/bot';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { InlineButton } from '$lib/api/types';
	import { inlineRows, safeUrl, type ChatMessage } from '$lib/game/chat';
	import { fmtMoment, fmtTime } from '$lib/util/format';

	interface Props {
		message: ChatMessage;
		/** callback_data кнопки этого сообщения, нажатие которой ещё идёт. */
		pending: string | null;
		/** Кнопки не жмутся: движок не запущен или идёт другое нажатие. */
		disabled: boolean;
		onclick: (button: InlineButton) => void;
	}
	let { message, pending, disabled, onclick }: Props = $props();
	const rows = $derived(inlineRows(message.markup));
	const out = $derived(message.outgoing);
	const width = 'max-w-[88%] md:max-w-[70%]';
</script>

<article data-msg={message.msg_id} data-side={out ? 'out' : 'in'} class="flex flex-col {out ? 'items-end' : 'items-start'}">
	<div
		class="{width} rounded-2xl px-3 py-1.5 text-sm {out
			? 'rounded-br-md bg-accent-soft'
			: 'rounded-bl-md border border-line-soft bg-surface'}"
	>
		<p class="ext-text">{message.text ?? '(без текста)'}</p>
		<p class="mt-0.5 flex items-center justify-end gap-1.5 text-[11px] leading-4 text-fg-faint tabular-nums">
			{#if message.source === 'planner'}
				<span class="inline-flex items-center gap-0.5 text-fg-muted" title="отправил бот">
					<Bot class="size-3" aria-hidden="true" />бот
				</span>
			{/if}
			{#if message.edited}<span>изм.</span>{/if}
			<time datetime={message.date} title={fmtMoment(message.date, new Date(), true)}>{fmtTime(message.date)}</time>
		</p>
	</div>
	{#if rows.length > 0}
		<div class="mt-1 w-full {width} space-y-1" role="group" aria-label="Кнопки сообщения">
			{#each rows as row, r (r)}
				<div class="flex gap-1">
					{#each row as b (`${b[1]}:${b[2]}`)}
						{@const url = safeUrl(b[4])}
						{#if b[3]}
							<button
								type="button"
								class="btn min-h-8 min-w-0 flex-1 text-xs"
								disabled={disabled || pending !== null}
								aria-busy={pending === b[3] ? 'true' : undefined}
								onclick={() => onclick(b)}
							>
								{#if pending === b[3]}<LoaderCircle class="size-3.5 shrink-0 animate-spin" aria-hidden="true" />{/if}
								<span class="ext-text">{b[0]}</span>
							</button>
						{:else if url}
							<a class="btn min-h-8 min-w-0 flex-1 text-xs" href={url} target="_blank" rel="noopener noreferrer">
								<span class="ext-text">{b[0]}</span>
								<ExternalLink class="size-3 shrink-0" aria-hidden="true" />
							</a>
						{:else}
							<button type="button" class="btn min-h-8 min-w-0 flex-1 text-xs" disabled title="Работает только в Telegram">
								<span class="ext-text">{b[0]}</span>
							</button>
						{/if}
					{/each}
				</div>
			{/each}
		</div>
	{/if}
</article>
