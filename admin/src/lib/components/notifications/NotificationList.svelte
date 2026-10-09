<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { NotificationOut } from '$lib/api/types';
	import type { LiveEvent } from '$lib/live/sse';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment } from '$lib/util/format';
	import Pill from '../Pill.svelte';

	type Level = 'info' | 'warn' | 'error';

	interface Props {
		api: AccountApi;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		/** Прочитано на сервере — перечитать счётчик в меню. */
		onread?: () => void;
		now?: Date;
		/** Непрочитанных на сервере и идёт ли отметка — для кнопки «Прочитать всё» в шапке страницы. */
		unread?: number;
		busy?: boolean;
	}
	let {
		api,
		subscribe,
		onread,
		now: fixedNow,
		unread = $bindable(0),
		busy = $bindable(false)
	}: Props = $props();
	const now = $derived(fixedNow ?? clock.now);
	let items = $state<NotificationOut[]>([]);
	let next = $state<number | null>(null);
	let onlyUnread = $state(false);
	let level = $state<Level | ''>('');
	let error = $state('');

	async function load(before: number | null) {
		try {
			const page = await call(
				api.GET('/notifications', {
					params: {
						query: {
							unread: onlyUnread,
							limit: 50,
							...(level ? { level } : {}),
							...(before ? { before } : {})
						}
					}
				})
			);
			items = before ? [...items, ...page.items] : page.items;
			unread = page.unread;
			next = page.next_before;
			error = '';
		} catch (e) {
			error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void onlyUnread;
		void level;
		void load(null);
	});

	$effect(() => {
		if (!subscribe) return;
		return subscribe((e) => {
			if (e.type === 'reset') return void load(null);
			if (e.type !== 'notification') return;
			unread += 1;
			if (level && e.data.level !== level) return;
			const d = e.data;
			items = [{ ...d, created_at: new Date().toISOString(), read: false }, ...items.filter((i) => i.id !== d.id)];
		});
	});

	/** Отметить прочитанным всё до последнего показанного. */
	export async function readAll() {
		const top = Math.max(0, ...items.map((i) => i.id));
		if (top === 0) return;
		busy = true;
		try {
			const out = await call(api.POST('/notifications/read', { body: { up_to_id: top } }));
			toasts.show(`Прочитано: ${out.read}`, 'ok');
			onread?.();
			await load(null);
		} catch (e) {
			toasts.show(e instanceof ApiFailure ? e.message : String(e), 'error');
		} finally {
			busy = false;
		}
	}

	const tone = (l: string) => (l === 'error' ? 'bad' : l === 'warn' ? 'warn' : 'muted');
</script>

<div class="flex flex-wrap items-center gap-1.5">
	<button type="button" class="chip" aria-pressed={onlyUnread} onclick={() => (onlyUnread = !onlyUnread)}
		>непрочитанные ({unread})</button
	>
	<label class="chip gap-1 pr-1">
		<span>уровень</span>
		<select class="bg-transparent text-xs text-fg focus:outline-none" bind:value={level}>
			<option value="">все</option>
			<option value="info">info</option>
			<option value="warn">warn</option>
			<option value="error">error</option>
		</select>
	</label>
</div>
{#if error}<p class="ext-text mt-2 text-sm text-bad-fg" role="alert">{error}</p>{/if}
<ul class="-mx-3.5 mt-3 divide-y divide-line-soft border-t border-line-soft" aria-label="Уведомления">
	{#each items as n (n.id)}
		<li class="flex flex-wrap items-baseline gap-2 px-3.5 py-2 text-sm {n.read ? 'text-fg-muted' : ''}">
			{#if !n.read}<span class="size-2 self-center rounded-full bg-accent" aria-label="не прочитано"></span>{/if}
			<Pill tone={tone(n.level)}>{n.level}</Pill>
			<span class="font-mono text-xs">{n.code}</span>
			<span class="ext-text min-w-0 flex-1 basis-60">{n.text}</span>
			<time class="text-xs text-fg-faint" datetime={n.created_at}>{fmtMoment(n.created_at, now)}</time>
		</li>
	{:else}
		<li class="px-3.5 py-2 text-sm text-fg-muted">Уведомлений нет.</li>
	{/each}
</ul>
{#if next !== null}
	<button type="button" class="btn mt-2 w-full" onclick={() => void load(next)}>Ещё</button>
{/if}
