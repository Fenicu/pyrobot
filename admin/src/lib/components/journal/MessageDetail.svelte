<script lang="ts">
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { components } from '$lib/api/schema';
	import type { InlineButton, Markup, MessageItem } from '$lib/api/types';

	type ClickIn = components['schemas']['ClickIn'];
	import { commandResult, isStale, newKey, withConfirm, type Confirmer } from '$lib/commands';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment } from '$lib/util/format';
	import { pretty } from '$lib/util/text';
	import KV from './KV.svelte';

	interface Props {
		api: Api;
		item: MessageItem;
		/** Сообщение изменилось (stale_revision/stale_button): перечитать ленту. */
		onstale?: () => void;
		confirmer?: Confirmer;
	}
	let { api, item, onstale, confirmer }: Props = $props();
	let pending = $state<string | null>(null);
	const now = $derived(clock.now);

	const markup = $derived((item.markup ?? null) as Markup | null);
	const rows = $derived.by(() => {
		const out: InlineButton[][] = [];
		for (const b of markup?.inline ?? []) (out[b[1]] ??= []).push(b);
		return out.filter(Boolean).map((r) => r.sort((a, b) => a[2] - b[2]));
	});

	async function click(button: InlineButton) {
		const [text, , , data] = button;
		if (!data) return;
		pending = data;
		// Один ключ на нажатие: повтор после подтверждения — тот же ключ.
		const body: ClickIn = {
			chat_id: item.chat_id,
			message_id: item.msg_id,
			revision: item.revision,
			callback_data: data,
			idempotency_key: newKey('click')
		};
		try {
			const out = await withConfirm<ClickIn>(
				(b) => call(api.POST('/api/v1/commands/click', { body: b })),
				body,
				`кнопка «${text}»`,
				confirmer
			);
			if (out) {
				const r = commandResult(out);
				toasts.show(r.text, r.kind);
				if (isStale(out)) onstale?.();
			}
		} catch (e) {
			toasts.show(e instanceof ApiFailure ? e.message : String(e), 'error');
		} finally {
			pending = null;
		}
	}
</script>

<h3 class="mb-1 font-semibold">
	Сообщение #{item.id} · {fmtMoment(item.at, now, true)}
</h3>
<dl class="mb-2">
	<KV label="Сообщение">{item.msg_id} · правка {item.revision}{item.kind === 'edit' ? ' (edit)' : ''}</KV>
	<KV label="Чат"><span class="font-mono text-xs">{item.chat_id}</span></KV>
	{#if item.outgoing}<KV label="Направление">отправлено ботом</KV>{/if}
	{#if item.recovered}<KV label="Догон">восстановлено после простоя</KV>{/if}
</dl>
<div class="ext-text rounded-md border border-line-soft bg-bg p-2 text-sm">{item.text ?? '(без текста)'}</div>

{#if rows.length > 0}
	<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">Кнопки</h4>
	<div class="space-y-1" role="group" aria-label="Кнопки сообщения">
		{#each rows as row, r (r)}
			<div class="flex flex-wrap gap-1">
				{#each row as b (`${b[1]}:${b[2]}`)}
					{#if b[3]}
						<button
							type="button"
							class="btn min-h-8 flex-1 text-xs"
							disabled={pending !== null}
							title={b[3]}
							onclick={() => click(b)}
						>
							<span class="ext-text">{pending === b[3] ? '…' : b[0]}</span>
						</button>
					{:else}
						<span class="chip flex-1 justify-center" title={b[4] ?? b[5] ?? ''}>
							<span class="ext-text">{b[0]}</span>
						</span>
					{/if}
				{/each}
			</div>
		{/each}
	</div>
{/if}
{#if markup?.reply?.length}
	<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">Клавиатура</h4>
	<div class="space-y-1">
		{#each markup.reply as row, r (r)}
			<div class="flex flex-wrap gap-1">
				{#each row as text, c (c)}<span class="chip"><span class="ext-text">{text}</span></span>{/each}
			</div>
		{/each}
	</div>
{/if}
{#if item.events.length > 0}
	<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">События разбора</h4>
	<pre class="ext-text rounded bg-bg p-2 font-mono text-xs">{pretty(item.events)}</pre>
{/if}
