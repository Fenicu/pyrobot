<script lang="ts">
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import Hand from '@lucide/svelte/icons/hand';
	import { onMount, tick, untrack } from 'svelte';
	import type { AccountApi } from '$lib/api/account';
	import { ApiFailure, errorText } from '$lib/api/errors';
	import type { CommandOut, EngineStatus, InlineButton } from '$lib/api/types';
	import { clickButton, commandResult, newKey, sendText, type Confirmer } from '$lib/commands';
	import { replyKeyboard, type ChatMessage } from '$lib/game/chat';
	import type { ManualControl } from '$lib/game/control.svelte';
	import type { GameChat } from '$lib/game/store.svelte';
	import type { LiveEvent } from '$lib/live/sse';
	import { scenarioText } from '$lib/plan/text';
	import { fmtTime } from '$lib/util/format';
	import Page from '../shell/Page.svelte';
	import ChatBubble from './ChatBubble.svelte';
	import Composer from './Composer.svelte';

	interface Props {
		api: AccountApi;
		chat: GameChat;
		control: ManualControl;
		/** Статус движка: не запущен — команды не уходят; срок ручного управления и идущий сценарий. */
		status: EngineStatus | null;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
		confirmer?: Confirmer;
		/** Управление взято или возвращено: перечитать статус движка и список аккаунтов. */
		onchange?: () => void;
	}
	let { api, chat, control, status, subscribe, confirmer, onchange }: Props = $props();

	let log = $state<HTMLElement | null>(null);
	let atBottom = $state(true);
	let fresh = $state(false);
	let sending = $state(false);
	let clicking = $state<{ msg: number; data: string } | null>(null);
	let seen: number | null = null;
	let scrolled = false;

	const keyboard = $derived(replyKeyboard(chat.messages));
	const blocked = $derived(status?.running === false ? 'Движок не запущен — команды в игру не уходят' : '');
	// Итог, который придёт сообщением игры, строкой не дублируется.
	const SILENT = new Set(['confirmed', 'sent', 'intent']);
	const NOTE_TONE: Record<string, string> = {
		ok: 'text-ok-fg',
		warn: 'text-warn-fg',
		error: 'text-bad-fg',
		info: 'text-fg-muted'
	};

	// Срок из опроса статуса — только когда он сменился: свежий кадр потока не перетирается тем же
	// значением опроса.
	const polledUntil = $derived(status?.manual_control_until ?? null);
	$effect(() => control.sync(polledUntil));

	onMount(() => {
		void chat.start();
		const off = subscribe?.((e) => {
			chat.onEvent(e);
			control.onEvent(e);
		});
		return () => {
			off?.();
			control.stop();
		};
	});

	async function toggle(on: boolean) {
		await (on ? control.take() : control.release());
		onchange?.();
	}

	function toBottom() {
		if (log) log.scrollTop = log.scrollHeight;
		atBottom = true;
		fresh = false;
	}

	// После отрисовки: первая загрузка — вниз; новое сообщение — вниз, если читают низ, иначе
	// значок «новые»; правка и строка итога — вниз только у читающих низ.
	$effect(() => {
		void chat.entries;
		const newest = chat.newest;
		const loaded = chat.loaded;
		untrack(() => {
			if (!loaded) return;
			const grew = seen !== null && newest !== null && newest > seen;
			seen = newest;
			if (!scrolled || atBottom) {
				scrolled = true;
				toBottom();
			} else if (grew) {
				fresh = true;
			}
		});
	});

	async function older() {
		const el = log;
		if (!el || chat.loadingOlder) return;
		const fromBottom = el.scrollHeight - el.scrollTop;
		await chat.loadOlder();
		await tick();
		el.scrollTop = el.scrollHeight - fromBottom;
	}

	function onscroll() {
		if (!log) return;
		atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 48;
		if (atBottom) fresh = false;
		if (log.scrollTop < 64 && chat.hasOlder && !chat.loadingOlder && chat.olderError === null) void older();
	}

	function failure(e: unknown): string {
		return e instanceof ApiFailure ? e.message : String(e);
	}

	function report(what: string, out: CommandOut | null) {
		if (out === null) chat.note(`${what}: отменено`, 'info');
		else if (!SILENT.has(out.status)) {
			const r = commandResult(out);
			chat.note(`${what}: ${r.text}`, r.kind);
		}
	}

	async function send(text: string) {
		if (sending) return;
		sending = true;
		atBottom = true;
		try {
			report(`«${text}»`, await sendText(api, text, newKey('send'), confirmer));
		} catch (e) {
			chat.note(`«${text}»: ${failure(e)}`, 'error');
		} finally {
			sending = false;
		}
	}

	async function click(message: ChatMessage, button: InlineButton) {
		const data = button[3];
		if (!data || clicking !== null) return;
		clicking = { msg: message.msg_id, data };
		try {
			report(`«${button[0]}»`, await clickButton(api, message, button, confirmer));
		} catch (e) {
			chat.note(`«${button[0]}»: ${failure(e)}`, 'error');
		} finally {
			clicking = null;
		}
	}
</script>

<Page title="Игра">
	<!-- На телефоне чат — на весь экран между верхней полосой и вкладками, поле ввода внизу. -->
	<section
		class="flex flex-col overflow-hidden bg-bg max-md:fixed max-md:inset-x-0 max-md:top-[42px] max-md:bottom-[calc(3.4rem+env(safe-area-inset-bottom))] max-md:z-20 md:h-[calc(100dvh-5.5rem)] md:min-h-96 md:rounded-card md:border md:border-line"
		aria-label="Чат с игрой"
	>
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-surface px-3 py-2 text-sm">
			{#if control.until}
				<Hand class="size-4 shrink-0 text-accent" aria-hidden="true" />
				<span class="font-medium">Управление у тебя до {fmtTime(control.until)}</span>
				{#if status?.scenario}
					<span class="text-xs text-fg-muted">бот заканчивает: {scenarioText(status.scenario)}</span>
				{/if}
				<button type="button" class="btn ml-auto" disabled={control.busy} onclick={() => toggle(false)}>
					Вернуть боту
				</button>
			{:else}
				<span class="text-xs text-fg-muted">Играет бот</span>
				<button
					type="button"
					class="btn btn-primary ml-auto"
					disabled={control.busy || blocked !== ''}
					title="Бот не начнёт новых действий 30 минут; каждая твоя команда продлевает срок"
					onclick={() => toggle(true)}
				>
					<Hand class="size-4" aria-hidden="true" />Взять управление
				</button>
			{/if}
			{#if control.error}<p class="ext-text w-full text-xs text-bad-fg" role="alert">{control.error}</p>{/if}
		</div>
		<div class="relative min-h-0 flex-1">
			<div
				bind:this={log}
				class="h-full space-y-2 overflow-y-auto overscroll-contain px-3 py-3"
				role="log"
				aria-label="Сообщения игры"
				{onscroll}
			>
				{#if chat.hasOlder}
					<div class="flex justify-center">
						<button type="button" class="chip" disabled={chat.loadingOlder} onclick={older}>
							{chat.loadingOlder ? 'Загрузка…' : 'Показать раньше'}
						</button>
					</div>
				{/if}
				{#if chat.olderError}
					<p class="text-center text-xs text-bad-fg">{errorText(chat.olderError)}</p>
				{/if}
				{#if chat.error}
					<div class="flex flex-col items-center gap-2 py-8 text-sm">
						<p class="ext-text text-bad-fg" role="alert">{errorText(chat.error)}</p>
						<button type="button" class="btn" disabled={chat.loading} onclick={() => void chat.start()}>Повторить</button>
					</div>
				{:else if !chat.loaded}
					<p class="py-8 text-center text-sm text-fg-muted">Загрузка…</p>
				{:else if chat.entries.length === 0}
					<p class="py-8 text-center text-sm text-fg-muted">Сообщений с игрой пока нет</p>
				{/if}
				{#each chat.entries as entry (entry.key)}
					{#if entry.type === 'message'}
						{@const m = entry.message}
						<ChatBubble
							message={m}
							pending={clicking?.msg === m.msg_id ? clicking.data : null}
							disabled={blocked !== '' || clicking !== null}
							onclick={(b) => click(m, b)}
						/>
					{:else}
						<p class="ext-text text-center text-xs {NOTE_TONE[entry.note.kind] ?? ''}">{entry.note.text}</p>
					{/if}
				{/each}
			</div>
			{#if fresh}
				<button type="button" class="chip absolute bottom-3 left-1/2 -translate-x-1/2 bg-surface shadow" onclick={toBottom}>
					<ArrowDown class="size-3.5" aria-hidden="true" />новые
				</button>
			{/if}
		</div>
		<div class="pb-0">
			<Composer {keyboard} {blocked} busy={sending} onsend={(t) => void send(t)} />
		</div>
	</section>
</Page>
