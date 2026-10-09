<script lang="ts">
	import Ellipsis from '@lucide/svelte/icons/ellipsis';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Power from '@lucide/svelte/icons/power';
	import PowerOff from '@lucide/svelte/icons/power-off';
	import Send from '@lucide/svelte/icons/send';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Unplug from '@lucide/svelte/icons/unplug';
	import { onMount } from 'svelte';
	import { TONE_LABEL, accountActivity, accountTone, type Activity } from '$lib/accounts/status';
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { AccountOut } from '$lib/api/types';
	import { accountHref } from '$lib/nav';
	import type { AccountsStore } from '$lib/stores/accounts.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { reasonText } from '$lib/util/accounts';
	import { fmtTime } from '$lib/util/format';
	import { accountTitle } from '$lib/util/game';
	import AccountsPending from '../AccountsPending.svelte';
	import Modal from '../Modal.svelte';
	import Page from '../shell/Page.svelte';
	import StatusDot from '../ui/StatusDot.svelte';
	import CreateAccountForm from './CreateAccountForm.svelte';

	interface Props {
		api: Api;
		store: AccountsStore;
	}
	let { api, store }: Props = $props();

	// Опрос списка, пока какой-то аккаунт удаляется: чистка в фоне, исчезнуть он должен сразу после.
	const DELETING_POLL_MS = 3000;
	const ACTIVITY_COLOR = { muted: 'text-fg-muted', warn: 'text-warn-fg', bad: 'text-bad-fg' } as const;
	const uid = $props.id();

	let busy = $state(false);
	let listError = $state('');
	let creating = $state(false);
	let menuFor = $state<number | null>(null);
	let renaming = $state<AccountOut | null>(null);
	let newName = $state('');
	let renameError = $state('');
	let removing = $state<AccountOut | null>(null);
	let confirmName = $state('');
	let removeError = $state('');
	// «учёба до …» кончается между опросами списка — по часам страницы.
	let now = $state(new Date());

	const list = $derived(store.list);
	const hasDeleting = $derived(list?.some((a) => a.status === 'deleting') ?? false);
	// Имя сверяется как есть: сервер сравнивает так же, и удаление необратимо.
	const nameMatches = $derived(removing !== null && confirmName === removing.name);

	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		return () => clearInterval(t);
	});

	$effect(() => {
		if (!hasDeleting) return;
		const timer = setInterval(() => void store.load(), DELETING_POLL_MS);
		return () => clearInterval(timer);
	});

	/** Подпись карточки: на экране управления блокировка видна и у выключенного, причина ошибки — словами. */
	function activity(a: AccountOut): Activity {
		if (a.blocked && a.status !== 'deleting') {
			const reason = a.blocked_reason ? `: ${a.blocked_reason}` : '';
			return { text: `Заблокирован владельцем сервера${reason}`, until: null, tone: 'bad' };
		}
		const act = accountActivity(a, now, { card: true });
		return a.status === 'error' && a.status_reason ? { ...act, text: reasonText(a.status_reason) } : act;
	}

	function tgOffline(a: AccountOut): boolean {
		return !a.tg.online && a.status !== 'disabled' && a.status !== 'deleting';
	}

	function closeMenuOutside(e: MouseEvent) {
		if (menuFor === null) return;
		const target = e.target as Element | null;
		if (!target?.closest(`[data-menu="${menuFor}"]`)) menuFor = null;
	}

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

	async function setEnabled(a: AccountOut, enabled: boolean) {
		menuFor = null;
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
		menuFor = null;
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
		menuFor = null;
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
</script>

<svelte:window
	onclick={closeMenuOutside}
	onkeydown={(e) => {
		if (e.key === 'Escape' && menuFor !== null) menuFor = null;
	}}
/>

{#snippet summary(a: AccountOut, act: Activity)}
	{@const tone = accountTone(a)}
	<div class="flex items-center gap-2">
		<StatusDot {tone} label={TONE_LABEL[tone]} />
		<span id="{uid}-name-{a.id}" class="ext-text min-w-0 truncate font-medium">{accountTitle(a)}</span>
		{#if tgOffline(a)}
			<span class="shrink-0 text-warn-fg" title="Telegram не в сети">
				<Unplug class="size-3.5" aria-hidden="true" /><span class="sr-only">Telegram не в сети</span>
			</span>
		{/if}
		{#if a.level != null}<span class="shrink-0 text-fg-muted">ур. {a.level}</span>{/if}
		{#if act.until}<span class="ml-auto shrink-0 text-fg-muted tabular-nums">{fmtTime(act.until)}</span>{/if}
	</div>
	<p id="{uid}-act-{a.id}" class="ext-text mt-1 truncate {ACTIVITY_COLOR[act.tone]}">{act.text}</p>
{/snippet}

{#snippet item(label: string, icon: typeof Pencil, onclick: () => void, danger = false)}
	{@const Icon = icon}
	<button
		type="button"
		class="flex w-full items-center gap-2 rounded-ctl px-2.5 py-2 text-left hover:bg-surface-2 {danger
			? 'text-bad-fg'
			: ''}"
		disabled={busy}
		{onclick}
	>
		<Icon class="size-4 shrink-0" aria-hidden="true" />{label}
	</button>
{/snippet}

<Page title="Аккаунты">
	{#snippet actions()}
		<button type="button" class="btn btn-primary" onclick={() => (creating = true)}>
			<Plus class="size-4" aria-hidden="true" />Добавить
		</button>
	{/snippet}

	{#if listError}<p class="ext-text card mb-3 text-sm text-warn-fg" role="alert">{listError}</p>{/if}

	{#if list === null}
		<AccountsPending error={store.error} onretry={() => void store.load()} />
	{:else if list.length === 0}
		<p class="text-sm text-fg-muted">Аккаунтов нет — создайте первый.</p>
	{:else}
		<ul class="grid gap-2.5 md:grid-cols-2 xl:grid-cols-3" aria-label="Аккаунты">
			{#each list as a (a.id)}
				{@const act = activity(a)}
				{@const open = menuFor === a.id}
				<li class="relative" data-menu={a.id}>
					<div
						class="flex items-start gap-1 rounded-card border bg-surface text-sm {act.tone === 'bad'
							? 'border-bad/60'
							: 'border-line'}"
					>
						{#if a.status === 'deleting'}
							<div class="min-w-0 flex-1 px-3.5 py-3 text-fg-faint">{@render summary(a, act)}</div>
						{:else}
							<a
								href={accountHref(a.id, '')}
								class="min-w-0 flex-1 rounded-card px-3.5 py-3 hover:bg-surface-2/60"
								aria-labelledby="{uid}-name-{a.id}"
								aria-describedby="{uid}-act-{a.id}"
							>
								{@render summary(a, act)}
							</a>
							<button
								type="button"
								class="btn btn-ghost mt-2 mr-1.5 size-8 min-h-0 shrink-0 p-0 text-fg-muted md:min-h-0"
								aria-label="Действия"
								title="Действия"
								aria-expanded={open}
								aria-controls="{uid}-menu-{a.id}"
								onclick={() => (menuFor = open ? null : a.id)}
							>
								<Ellipsis class="size-4" aria-hidden="true" />
							</button>
						{/if}
					</div>
					{#if open}
						<div
							id="{uid}-menu-{a.id}"
							class="absolute top-11 right-1.5 z-20 w-52 rounded-ctl border border-line bg-surface p-1 text-sm shadow-lg"
						>
							{#if a.status === 'enabled'}
								{@render item('Выключить', PowerOff, () => void setEnabled(a, false))}
							{:else if !a.blocked}
								{@render item('Включить', Power, () => void setEnabled(a, true))}
							{/if}
							{@render item('Переименовать', Pencil, () => startRename(a))}
							<a
								href={accountHref(a.id, '/telegram')}
								class="flex items-center gap-2 rounded-ctl px-2.5 py-2 hover:bg-surface-2"
								onclick={() => (menuFor = null)}
							>
								<Send class="size-4 shrink-0" aria-hidden="true" />Вход в Telegram
							</a>
							{@render item('Удалить', Trash2, () => startRemove(a), true)}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</Page>

{#if creating}
	<Modal title="Новый аккаунт" onclose={() => (creating = false)}>
		<CreateAccountForm {api} {store} autofocus oncreated={() => (creating = false)} />
	</Modal>
{/if}

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
