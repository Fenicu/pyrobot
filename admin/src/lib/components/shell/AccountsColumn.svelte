<script lang="ts">
	import ChevronsLeft from '@lucide/svelte/icons/chevrons-left';
	import ChevronsRight from '@lucide/svelte/icons/chevrons-right';
	import Plus from '@lucide/svelte/icons/plus';
	import Unplug from '@lucide/svelte/icons/unplug';
	import { onMount } from 'svelte';
	import { accountActivity, accountTone, type Tone } from '$lib/accounts/status';
	import type { Api } from '$lib/api/client';
	import type { AccountOut } from '$lib/api/types';
	import { switchHref } from '$lib/nav';
	import { accountTitle } from '$lib/util/game';
	import CreateAccountForm from '../accounts/CreateAccountForm.svelte';
	import Modal from '../Modal.svelte';
	import StatusDot from '../ui/StatusDot.svelte';

	interface Props {
		api: Api;
		/** Аккаунты учётки (list null — ещё не загружены). */
		store: { list: AccountOut[] | null; load(): Promise<void> };
		/** Аккаунт меню. */
		current: number | null;
		/** Путь экрана: другой аккаунт открывается на том же разделе. */
		path: string;
	}
	let { api, store, current, path }: Props = $props();

	const COLLAPSED = 'pyrobot.accountsCollapsed';
	const TONE_LABEL: Record<Tone, string> = {
		ok: 'работает',
		warn: 'требует внимания',
		bad: 'ошибка',
		off: 'выключен'
	};
	const ACTIVITY_COLOR = { muted: 'text-fg-muted', warn: 'text-warn-fg', bad: 'text-bad-fg' } as const;

	let collapsed = $state(readCollapsed());
	let creating = $state(false);
	// «учёба» кончается между опросами списка — по часам страницы.
	let now = $state(new Date());

	onMount(() => {
		const t = setInterval(() => (now = new Date()), 30_000);
		return () => clearInterval(t);
	});

	function readCollapsed(): boolean {
		try {
			return localStorage.getItem(COLLAPSED) === '1';
		} catch {
			return false;
		}
	}

	function toggle() {
		collapsed = !collapsed;
		try {
			localStorage.setItem(COLLAPSED, collapsed ? '1' : '0');
		} catch {
			// без localStorage — только до перезагрузки
		}
	}

	function initial(a: AccountOut): string {
		return a.name.replace(/\[[^\]]*\]/g, '').match(/[\p{L}\p{N}]/u)?.[0]?.toUpperCase() ?? '?';
	}

	function tgOffline(a: AccountOut): boolean {
		return !a.tg.online && a.status !== 'disabled' && a.status !== 'deleting';
	}
</script>

{#snippet row(a: AccountOut)}
	{@const tone = accountTone(a)}
	{@const activity = accountActivity(a, now)}
	<StatusDot {tone} label={TONE_LABEL[tone]} />
	{#if collapsed}
		<span class="text-xs font-semibold" aria-hidden="true">{initial(a)}</span>
	{:else}
		<span class="min-w-0 flex-1 truncate font-medium">{accountTitle(a)}</span>
		{#if tgOffline(a)}
			<span class="shrink-0 text-warn-fg" title="Telegram не в сети">
				<Unplug class="size-3" aria-hidden="true" /><span class="sr-only">Telegram не в сети</span>
			</span>
		{/if}
		<small class="max-w-[45%] shrink-0 truncate text-[10px] {ACTIVITY_COLOR[activity.tone]}">
			{a.level != null ? `${a.level} · ${activity.text}` : activity.text}
		</small>
	{/if}
{/snippet}

<aside
	class="sticky top-0 flex h-dvh shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-line bg-surface py-2.5 {collapsed
		? 'w-11 items-center px-1'
		: 'w-[190px] px-2'}"
	aria-label="Аккаунты"
>
	<div class="mb-1 flex items-center gap-1 {collapsed ? 'flex-col' : 'px-2'}">
		{#if !collapsed}
			<h2 class="flex-1 text-[11px] font-semibold tracking-[0.07em] text-fg-muted uppercase">Аккаунты</h2>
			<button
				type="button"
				class="btn btn-ghost size-6 min-h-0 p-0 md:min-h-0"
				aria-label="Добавить аккаунт"
				title="Добавить аккаунт"
				onclick={() => (creating = true)}
			>
				<Plus class="size-4" aria-hidden="true" />
			</button>
		{/if}
		<button
			type="button"
			class="btn btn-ghost size-6 min-h-0 p-0 text-fg-muted md:min-h-0"
			aria-label={collapsed ? 'Развернуть список аккаунтов' : 'Свернуть список аккаунтов'}
			title={collapsed ? 'Развернуть' : 'Свернуть'}
			aria-expanded={!collapsed}
			onclick={toggle}
		>
			{#if collapsed}
				<ChevronsRight class="size-4" aria-hidden="true" />
			{:else}
				<ChevronsLeft class="size-4" aria-hidden="true" />
			{/if}
		</button>
	</div>
	<ul class="flex flex-col gap-0.5 {collapsed ? 'items-center' : ''}" aria-label="Список аккаунтов">
		{#each store.list ?? [] as a (a.id)}
			{@const active = a.id === current}
			{@const cls = `flex items-center rounded-[8px] text-[13px] ${collapsed ? 'size-9 justify-center gap-1' : 'gap-[7px] px-2 py-1.5'}`}
			<li>
				{#if a.status === 'deleting'}
					<div class="{cls} text-fg-faint" title={accountTitle(a)}>{@render row(a)}</div>
				{:else}
					<a
						href={switchHref(path, a.id)}
						class="{cls} {active ? 'bg-surface-2 text-fg' : 'text-fg-muted hover:bg-surface-2 hover:text-fg'}"
						aria-current={active ? 'true' : undefined}
						aria-label={collapsed ? accountTitle(a) : undefined}
						title={collapsed ? `${accountTitle(a)} · ${accountActivity(a, now).text}` : undefined}
					>
						{@render row(a)}
					</a>
				{/if}
			</li>
		{/each}
	</ul>
	{#if !collapsed}
		<a href="/accounts" class="mt-1 px-2 text-xs text-accent hover:underline">управление</a>
	{/if}
</aside>

{#if creating}
	<Modal title="Новый аккаунт" onclose={() => (creating = false)}>
		<CreateAccountForm {api} {store} autofocus oncreated={() => (creating = false)} />
	</Modal>
{/if}
