<script lang="ts">
	import Pencil from '@lucide/svelte/icons/pencil';
	import Power from '@lucide/svelte/icons/power';
	import PowerOff from '@lucide/svelte/icons/power-off';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { goto } from '$app/navigation';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { AccountOut } from '$lib/api/types';
	import { accountHref } from '$lib/nav';
	import type { AccountsStore } from '$lib/stores/accounts.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { STATUS_LABEL, reasonText } from '$lib/util/accounts';
	import { fmtMoment } from '$lib/util/format';
	import { accountTitle } from '$lib/util/game';
	import AccountsPending from '../AccountsPending.svelte';
	import Modal from '../Modal.svelte';
	import Pill from '../Pill.svelte';

	interface Props {
		api: Api;
		store: AccountsStore;
	}
	let { api, store }: Props = $props();

	// Колонки таблицы на ПК; на телефоне каждый аккаунт — карточка, подписи полей — в ней самой.
	// Каждая строка — своя сетка: колонка кнопок фиксированной ширины (под три кнопки), иначе у
	// заголовка и строки «удаляется» без кнопок `fr`-дорожки делились бы иначе и колонки съезжали.
	const COLUMNS =
		'md:grid md:grid-cols-[minmax(0,1.3fr)_minmax(0,1.1fr)_minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,0.7fr)_9rem]';
	const HEADERS = ['Аккаунт', 'Telegram', 'Статус', 'Режим', 'Последнее действие', 'Внимание'];
	// Опрос списка, пока какой-то аккаунт удаляется: чистка в фоне, исчезнуть он должен сразу после.
	const DELETING_POLL_MS = 3000;

	let busy = $state(false);
	let name = $state('');
	let createError = $state('');
	let listError = $state('');
	let renaming = $state<AccountOut | null>(null);
	let newName = $state('');
	let renameError = $state('');
	let removing = $state<AccountOut | null>(null);
	let confirmName = $state('');
	let removeError = $state('');

	const list = $derived(store.list);
	const hasDeleting = $derived(list?.some((a) => a.status === 'deleting') ?? false);
	// Имя сверяется как есть: сервер сравнивает так же, и удаление необратимо.
	const nameMatches = $derived(removing !== null && confirmName === removing.name);

	$effect(() => {
		if (!hasDeleting) return;
		const timer = setInterval(() => void store.load(), DELETING_POLL_MS);
		return () => clearInterval(timer);
	});

	/** Запрос с общим замком кнопок; результат или null (текст ошибки — в `fail`). */
	async function run<T>(request: () => Promise<T>, fail: (text: string) => void): Promise<T | null> {
		busy = true;
		try {
			return await request();
		} catch (e) {
			fail(e instanceof ApiFailure ? e.message : String(e));
			return null;
		} finally {
			busy = false;
		}
	}

	async function create(e: SubmitEvent) {
		e.preventDefault();
		createError = '';
		const created = await run(
			() => call(api.POST('/api/v1/accounts', { body: { name: name.trim() } })),
			(text) => (createError = text)
		);
		if (created === null) return;
		name = '';
		// Макет аккаунта открывает только аккаунт из списка: сначала список, потом переход.
		await store.load();
		await goto(accountHref(created.id, '/telegram'));
	}

	async function setEnabled(a: AccountOut, enabled: boolean) {
		listError = '';
		const done = await run(
			() =>
				call(
					api.PATCH('/api/v1/accounts/{account_id}', { params: { path: { account_id: a.id } }, body: { enabled } })
				),
			(text) => (listError = text)
		);
		if (done !== null) await store.load();
	}

	function startRename(a: AccountOut) {
		renaming = a;
		newName = a.name;
		renameError = '';
	}

	async function rename(e: SubmitEvent) {
		e.preventDefault();
		const target = renaming;
		if (target === null) return;
		renameError = '';
		const done = await run(
			() =>
				call(
					api.PATCH('/api/v1/accounts/{account_id}', {
						params: { path: { account_id: target.id } },
						body: { name: newName.trim() }
					})
				),
			(text) => (renameError = text)
		);
		if (done === null) return;
		renaming = null;
		await store.load();
	}

	function startRemove(a: AccountOut) {
		removing = a;
		confirmName = '';
		removeError = '';
	}

	async function remove(e: SubmitEvent) {
		e.preventDefault();
		const target = removing;
		if (target === null || !nameMatches) return;
		removeError = '';
		const done = await run(
			() =>
				call(
					api.DELETE('/api/v1/accounts/{account_id}', {
						params: { path: { account_id: target.id } },
						body: { confirm_name: confirmName }
					})
				),
			(text) => (removeError = text)
		);
		if (done === null) return;
		removing = null;
		toasts.show(`Аккаунт «${target.name}» удаляется`, 'ok');
		// До конца чистки аккаунт остаётся в списке со статусом «удаляется».
		await store.load();
	}

	const tone = (a: AccountOut) =>
		a.status === 'enabled' ? 'ok' : a.status === 'error' ? 'bad' : a.status === 'deleting' ? 'warn' : 'muted';
	const now = new Date();
</script>

{#snippet label(text: string)}<span class="text-xs text-fg-muted md:hidden">{text}</span>{/snippet}

<div class="max-w-6xl space-y-3">
	<form class="card space-y-2" onsubmit={create}>
		<div class="flex flex-col gap-2 sm:flex-row sm:items-end">
			<label class="block flex-1 space-y-1">
				<span class="label">Имя нового аккаунта</span>
				<input class="input" bind:value={name} maxlength={64} autocomplete="off" required />
			</label>
			<button type="submit" class="btn btn-primary" disabled={busy || !name.trim()}>Создать</button>
		</div>
		{#if createError}<p class="ext-text text-sm text-warn-fg" role="alert">{createError}</p>{/if}
	</form>

	{#if listError}<p class="ext-text card text-sm text-warn-fg" role="alert">{listError}</p>{/if}

	{#if list === null}
		<AccountsPending error={store.error} onretry={() => void store.load()} />
	{:else if list.length === 0}
		<p class="text-sm text-fg-muted">Аккаунтов нет — создайте первый.</p>
	{:else}
		<div role="table" aria-label="Аккаунты" class="space-y-2 md:space-y-0 md:overflow-hidden md:rounded-lg md:border md:border-line md:bg-surface">
			<div
				role="row"
				class="hidden gap-3 border-b border-line-soft px-3 py-2 text-xs font-semibold tracking-wide text-fg-muted uppercase {COLUMNS}"
			>
				{#each HEADERS as header, i (i)}<span role="columnheader">{header}</span>{/each}
				<span role="columnheader" class="sr-only">Действия</span>
			</div>
			{#each list as a (a.id)}
				<div
					role="row"
					class="card grid gap-2 md:items-center md:gap-3 md:rounded-none md:border-0 md:border-b md:border-line-soft md:last:border-b-0 {COLUMNS}"
				>
					<div role="cell" class="min-w-0">
						{#if a.status === 'deleting'}
							<span class="ext-text font-medium">{accountTitle(a)}</span>
						{:else}
							<a class="ext-text font-medium text-accent hover:underline" href={accountHref(a.id, '')}>{accountTitle(a)}</a>
						{/if}
					</div>
					<div role="cell" class="flex flex-wrap items-center justify-between gap-1.5 md:justify-start">
						{@render label('Telegram')}
						<span class="flex flex-wrap items-center gap-1.5">
							{#if a.tg.user_id === null}
								<Pill>не подключён</Pill>
							{:else}
								<span class="font-mono text-sm">{a.tg.user_id}</span>
								<Pill tone={a.tg.online ? 'ok' : 'muted'}>{a.tg.online ? 'в сети' : 'не в сети'}</Pill>
							{/if}
						</span>
					</div>
					<div role="cell" class="flex flex-wrap items-center justify-between gap-1.5 md:block">
						{@render label('Статус')}
						<span class="min-w-0">
							<Pill tone={tone(a)}>{STATUS_LABEL[a.status]}</Pill>
							<!-- Причина blocked_by_owner без блокировки устарела после разблокировки. -->
							{#if a.blocked}
								<span class="ext-text mt-1 block text-xs text-bad-fg"
									>Заблокирован владельцем сервера{a.blocked_reason ? `: ${a.blocked_reason}` : ''}</span
								>
							{:else if a.status_reason && a.status_reason !== 'blocked_by_owner'}
								<span class="ext-text mt-1 block text-xs text-fg-muted">{reasonText(a.status_reason)}</span>
							{/if}
						</span>
					</div>
					<div role="cell" class="flex flex-wrap items-center justify-between gap-1.5 md:justify-start">
						{@render label('Режим')}
						<span class="flex flex-wrap items-center gap-1.5">
							{#if a.mode === 'live'}
								<Pill tone="ok">LIVE</Pill>
							{:else}
								<Pill tone="warn" title="Команды, кроме навигации, не уходят в игру">DRY RUN</Pill>
							{/if}
							{#if a.paused}<Pill tone="warn">пауза</Pill>{/if}
							{#if a.killed}<Pill tone="bad">kill</Pill>{/if}
						</span>
					</div>
					<div role="cell" class="flex items-center justify-between gap-1.5 text-sm md:block">
						{@render label('Последнее действие')}
						<span>{a.last_action_at ? fmtMoment(a.last_action_at, now) : '—'}</span>
					</div>
					<div role="cell" class="flex items-center justify-between gap-1.5 md:justify-start">
						{@render label('Внимание')}
						<span class="flex items-center gap-1.5">
							{#if a.unread.warn + a.unread.error === 0}
								<span class="text-sm text-fg-muted">—</span>
							{/if}
							{#if a.unread.warn > 0}
								<span class="pill pill-warn" aria-label="предупреждений: {a.unread.warn}">{a.unread.warn}</span>
							{/if}
							{#if a.unread.error > 0}
								<span class="pill pill-bad" aria-label="ошибок: {a.unread.error}">{a.unread.error}</span>
							{/if}
						</span>
					</div>
					<div role="cell" class="flex flex-wrap gap-1.5 md:justify-end">
						{#if a.status !== 'deleting'}
							{#if a.status === 'enabled'}
								<button type="button" class="btn" title="Выключить" disabled={busy} onclick={() => setEnabled(a, false)}>
									<PowerOff class="size-4" aria-hidden="true" /><span class="md:sr-only">Выключить</span>
								</button>
							{:else if !a.blocked}
								<button type="button" class="btn" title="Включить" disabled={busy} onclick={() => setEnabled(a, true)}>
									<Power class="size-4" aria-hidden="true" /><span class="md:sr-only">Включить</span>
								</button>
							{/if}
							<button type="button" class="btn" title="Переименовать" disabled={busy} onclick={() => startRename(a)}>
								<Pencil class="size-4" aria-hidden="true" /><span class="md:sr-only">Переименовать</span>
							</button>
							<button type="button" class="btn btn-danger" title="Удалить" disabled={busy} onclick={() => startRemove(a)}>
								<Trash2 class="size-4" aria-hidden="true" /><span class="md:sr-only">Удалить</span>
							</button>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if renaming}
	<Modal title="Переименовать аккаунт" onclose={() => (renaming = null)}>
		<form id="rename-form" class="space-y-2" onsubmit={rename}>
			<label class="block space-y-1">
				<span class="label">Новое имя</span>
				<input class="input" bind:value={newName} maxlength={64} autocomplete="off" required data-autofocus />
			</label>
			{#if renameError}<p class="ext-text text-sm text-warn-fg" role="alert">{renameError}</p>{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (renaming = null)}>Отмена</button>
			<button
				type="submit"
				form="rename-form"
				class="btn btn-primary"
				disabled={busy || !newName.trim() || newName.trim() === renaming?.name}>Сохранить</button
			>
		{/snippet}
	</Modal>
{/if}

{#if removing}
	<Modal title="Удалить аккаунт?" onclose={() => (removing = null)}>
		<form id="remove-form" class="space-y-3" onsubmit={remove}>
			<p class="text-sm text-fg-muted">
				Аккаунт «<span class="ext-text">{removing.name}</span>» будет удалён навсегда: движок остановится, сервис выйдет
				из сессии Telegram, журнал, настройки и привязка к пользователю Telegram пропадут. Отменить нельзя.
			</p>
			<label class="block space-y-1">
				<span class="label">Имя аккаунта для подтверждения</span>
				<input class="input" bind:value={confirmName} autocomplete="off" placeholder={removing.name} data-autofocus />
			</label>
			{#if removeError}<p class="ext-text text-sm text-warn-fg" role="alert">{removeError}</p>{/if}
		</form>
		{#snippet footer()}
			<button type="button" class="btn" onclick={() => (removing = null)}>Отмена</button>
			<button type="submit" form="remove-form" class="btn btn-danger" disabled={busy || !nameMatches}
				>Удалить навсегда</button
			>
		{/snippet}
	</Modal>
{/if}
