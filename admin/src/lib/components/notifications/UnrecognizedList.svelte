<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { UnrecognizedOut } from '$lib/api/types';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment } from '$lib/util/format';

	type Acked = 'false' | 'true' | 'all';
	const MAX_ACK = 500;

	interface Props {
		api: AccountApi;
		now?: Date;
	}
	let { api, now: fixedNow }: Props = $props();
	const now = $derived(fixedNow ?? clock.now);
	let items = $state<UnrecognizedOut[]>([]);
	let next = $state<number | null>(null);
	let acked = $state<Acked>('false');
	let selected = $state<Set<number>>(new Set());
	let open = $state<number | null>(null);
	let error = $state('');
	let busy = $state(false);

	async function load(before: number | null) {
		try {
			const page = await call(
				api.GET('/unrecognized', {
					params: { query: { acked, limit: 50, ...(before ? { before } : {}) } }
				})
			);
			items = before ? [...items, ...page.items] : page.items;
			next = page.next_before;
			error = '';
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void acked;
		selected = new Set();
		void load(null);
	});

	function toggle(id: number) {
		const s = new Set(selected);
		if (s.has(id)) s.delete(id);
		else s.add(id);
		selected = s;
	}

	async function ack() {
		const ids = [...selected].slice(0, MAX_ACK);
		if (ids.length === 0) return;
		busy = true;
		try {
			const out = await call(api.POST('/unrecognized/ack', { body: { ids } }));
			toasts.show(`Разобрано: ${out.acked}`, 'ok');
			selected = new Set();
			await load(null);
		} catch (e) {
			toasts.show(e instanceof ApiFailure ? e.message : String(e), 'error');
		} finally {
			busy = false;
		}
	}

	const pending = $derived(items.filter((i) => !i.acked));
	const allSelected = $derived(pending.length > 0 && pending.every((i) => selected.has(i.id)));
</script>

<div class="flex flex-wrap items-center gap-1.5">
	{#each [['false', 'неразобранные'], ['true', 'разобранные'], ['all', 'все']] as [value, label] (value)}
		<button type="button" class="chip" aria-pressed={acked === value} onclick={() => (acked = value as Acked)}>{label}</button>
	{/each}
	<label class="ml-auto inline-flex items-center gap-1 text-xs text-fg-muted">
		<input
			type="checkbox"
			checked={allSelected}
			disabled={pending.length === 0}
			onchange={(e) => (selected = e.currentTarget.checked ? new Set(pending.map((i) => i.id)) : new Set())}
		/>
		выбрать все
	</label>
	<button type="button" class="btn" disabled={busy || selected.size === 0} onclick={ack}>
		Отметить разобранными ({selected.size})
	</button>
</div>
{#if error}<p class="ext-text mt-2 text-sm text-bad-fg" role="alert">{error}</p>{/if}
<ul class="-mx-3.5 mt-3 divide-y divide-line-soft border-t border-line-soft" aria-label="Нераспознанные сообщения">
	{#each items as u (u.id)}
		<li class="px-3.5 py-2 text-sm">
			<div class="flex items-baseline gap-2">
				{#if !u.acked}
					<input
						type="checkbox"
						checked={selected.has(u.id)}
						aria-label="Выбрать #{u.id}"
						onchange={() => toggle(u.id)}
					/>
				{/if}
				<button
					type="button"
					class="min-w-0 flex-1 truncate text-left"
					aria-expanded={open === u.id}
					onclick={() => (open = open === u.id ? null : u.id)}
				>
					<span class="ext-text {u.acked ? 'text-fg-muted' : ''}">{u.first_line}</span>
				</button>
				<time class="shrink-0 text-xs text-fg-faint" datetime={u.created_at}>{fmtMoment(u.created_at, now)}</time>
			</div>
			{#if open === u.id}
				<div class="ext-text mt-2 rounded-md border border-line-soft bg-bg p-2 text-sm">{u.text ?? '(текста нет)'}</div>
				<p class="mt-1 font-mono text-xs text-fg-faint">сообщение {u.msg_id} · запись журнала {u.message_id}</p>
			{/if}
		</li>
	{:else}
		<li class="px-3.5 py-2 text-sm text-fg-muted">Нераспознанных сообщений нет.</li>
	{/each}
</ul>
{#if next !== null}
	<button type="button" class="btn mt-2 w-full" onclick={() => void load(next)}>Ещё</button>
{/if}
