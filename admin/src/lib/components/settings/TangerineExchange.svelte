<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure, codeText, errorText, normalizeError, type ApiError } from '$lib/api/errors';
	import type { AccountOut } from '$lib/api/types';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import TangerinePartner from '../TangerinePartner.svelte';

	interface Props {
		api: AccountApi;
		accountId: number;
		/** Аккаунты учётки; null — список ещё не загружен. */
		accounts: AccountOut[] | null;
		/** Настройка `chats.tangerine_reply_to` этого аккаунта записана парой — перечитать форму. */
		onpaired: () => void;
		/** Сохранённый `chats.tangerine_reply_to`: задан — под заголовком, кому дарим. */
		replyTo?: number | null;
	}
	let { api, accountId, accounts, onpaired, replyTo = null }: Props = $props();

	interface PairPartial {
		detail: 'tangerine_pair_partial';
		reason: string;
		posted: Record<string, number>;
		failed: number;
		written: number[];
	}

	let text = $state('🍊');
	let partner = $state<number | null>(null);
	let busy = $state(false);
	let postedId = $state<number | null>(null);
	let paired = $state('');
	let error = $state('');

	const message = $derived(text.trim());
	// Длина — в символах Unicode, как у сервера.
	const valid = $derived(message.length > 0 && [...message].length <= 200);
	const partners = $derived((accounts ?? []).filter((a) => a.id !== accountId));
	const partnerOnline = $derived(partners.some((a) => a.id === partner && a.tg.online));

	const nameOf = (id: number) => accounts?.find((a) => a.id === id)?.name ?? `аккаунт ${id}`;

	function failText(err: ApiError): string {
		if (err.kind === 'rate_limited' && err.code === 'flood_wait') {
			return err.retryAfter !== null ? `Telegram просит подождать ${err.retryAfter} с` : 'Telegram просит подождать';
		}
		if (err.kind === 'unavailable' && err.status === 502 && err.code && codeText(err.code) === undefined) {
			return `Telegram отказал: ${err.code}`;
		}
		return errorText(err);
	}

	function isPartial(body: unknown): body is PairPartial {
		return typeof body === 'object' && body !== null && (body as { detail?: unknown }).detail === 'tangerine_pair_partial';
	}

	function partialText(p: PairPartial, reason: string): string {
		const ids = Object.keys(p.posted).map(Number);
		if (ids.length < 2) {
			const [first] = ids;
			const sent = first === undefined ? '' : `; сообщение ${nameOf(first)} (id ${p.posted[first]}) осталось в чате`;
			return `${nameOf(p.failed)} не смог написать (${reason})${sent}, настройки не изменены`;
		}
		// Этот аккаунт — первым.
		const pair = ids.sort((a, b) => Number(b === accountId) - Number(a === accountId));
		const other = (id: number) => pair.find((x) => x !== id)!;
		const both = pair.map((id) => `${nameOf(id)} — id ${p.posted[id]}`).join(', ');
		const done = p.written.length > 0 ? `у ${p.written.map(nameOf).join(', ')} записано. ` : '';
		const manual = pair
			.filter((id) => !p.written.includes(id))
			.map((id) => `${nameOf(id)} — ${p.posted[other(id)]}`)
			.join(', ');
		return `Оба сообщения в чате: ${both}. Настройки записались не у всех (${reason}): ${done}Впиши вручную в „Сообщение для /gt“: ${manual}`;
	}

	async function post() {
		if (!valid || busy) return;
		busy = true;
		error = '';
		paired = '';
		postedId = null;
		try {
			const out = await call(api.POST('/tangerine/post', { body: { text: message } }));
			postedId = out.message_id;
		} catch (e) {
			error = e instanceof ApiFailure ? failText(e.error) : String(e);
		} finally {
			busy = false;
		}
	}

	async function copy() {
		if (postedId === null) return;
		try {
			await navigator.clipboard.writeText(String(postedId));
			toasts.show('id скопирован', 'ok');
		} catch {
			toasts.show('Не удалось скопировать id', 'error');
		}
	}

	async function pair() {
		const id = partner;
		if (!valid || busy || id === null || !partnerOnline) return;
		const sent = message;
		const ok = await dialogs.confirm({
			title: 'Связать аккаунты?',
			body: `Оба аккаунта вступят в чат мандаринов и напишут „${sent}“; каждому впишется сообщение другого`,
			confirmText: 'Связать'
		});
		if (!ok) return;
		busy = true;
		error = '';
		paired = '';
		postedId = null;
		try {
			const res = await api.POST('/tangerine/pair', { body: { partner_id: id, text: sent } });
			if (res.response.ok && res.data) {
				paired = `Связано: ${nameOf(res.data.account.id)} ↔ ${nameOf(res.data.partner.id)}`;
				onpaired();
				return;
			}
			const body: unknown = res.error;
			const { status, headers } = res.response;
			if (isPartial(body)) {
				error = partialText(body, failText(normalizeError(status, { detail: body.reason }, headers)));
				if (body.written.includes(accountId)) onpaired();
			} else if (
				typeof body === 'object' &&
				body !== null &&
				(body as { detail?: unknown }).detail === 'tg_not_online' &&
				typeof (body as { account_id?: unknown }).account_id === 'number'
			) {
				error = `Telegram не в сети у ${nameOf((body as { account_id: number }).account_id)} — ничего не отправлено`;
			} else {
				error = failText(normalizeError(status, body, headers));
			}
		} catch (e) {
			error = failText({ kind: 'network', status: 0, message: String(e) });
		} finally {
			busy = false;
		}
	}
</script>

<section class="mt-4 space-y-3 rounded-lg border border-line p-3 text-sm" aria-labelledby="tangerine-exchange-title">
	<h3 id="tangerine-exchange-title" class="font-medium">Обмен мандаринами</h3>
	{#if replyTo !== null}<TangerinePartner {api} refresh={replyTo} />{/if}
	<label class="block space-y-1">
		<span class="label">Текст сообщения</span>
		<input class="input" type="text" bind:value={text} />
	</label>
	{#if !valid}<p class="text-xs text-bad-fg">Текст: от 1 до 200 символов</p>{/if}

	<div class="space-y-2">
		<button type="button" class="btn w-full sm:w-auto" disabled={busy || !valid} onclick={post}>
			Вступить и написать
		</button>
		{#if postedId !== null}
			<div class="flex flex-wrap items-center gap-2">
				<span class="font-medium">Сообщение отправлено: id {postedId}</span>
				<button type="button" class="btn" onclick={copy}>Копировать</button>
			</div>
			<p class="text-xs text-fg-muted">Впиши этот id другому аккаунту в „Сообщение для /gt“</p>
		{/if}
	</div>

	{#if accounts === null}
		<p class="text-fg-muted">Загрузка аккаунтов…</p>
	{:else if partners.length === 0}
		<p class="text-fg-muted">Других аккаунтов нет</p>
	{:else}
		<div class="flex flex-col gap-2 sm:flex-row sm:items-end">
			<label class="block min-w-0 flex-1 space-y-1">
				<span class="label">Обмениваться с…</span>
				<select class="input" bind:value={partner}>
					<option value={null}>— выберите аккаунт —</option>
					{#each partners as a (a.id)}
						<option value={a.id} disabled={!a.tg.online}>
							{a.tg.online ? a.name : `${a.name} (Telegram не в сети)`}
						</option>
					{/each}
				</select>
			</label>
			<button type="button" class="btn w-full sm:w-auto sm:shrink-0" disabled={busy || !valid || !partnerOnline} onclick={pair}>
				Связать
			</button>
		</div>
	{/if}

	{#if paired}<p class="text-ok-fg" role="status">{paired}</p>{/if}
	{#if error}<p class="ext-text text-bad-fg" role="alert">{error}</p>{/if}
</section>
