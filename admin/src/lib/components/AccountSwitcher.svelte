<script lang="ts">
	import ChevronsUpDown from '@lucide/svelte/icons/chevrons-up-down';
	import type { AccountOut } from '$lib/api/types';
	import { switchHref } from '$lib/nav';
	import { accountTitle } from '$lib/util/game';

	interface Props {
		/** Аккаунты учётки (null — список ещё не загружен). */
		accounts: AccountOut[] | null;
		/** Аккаунт меню. */
		current: number | null;
		/** Непрочитанные warn и error аккаунта меню из его потока — свежее списка (null — нет потока). */
		alerts?: number | null;
		/** Путь экрана: другой аккаунт открывается на том же разделе. */
		path: string;
		/** side — вверху бокового меню ПК; bar — плашка вверху экрана телефона; list — список в «Ещё». */
		variant: 'side' | 'bar' | 'list';
		/** Плашка: открыть «Ещё» со списком аккаунтов. */
		onopen?: () => void;
		/** Выбран аккаунт (закрыть «Ещё»). */
		onpick?: () => void;
	}
	let { accounts, current, alerts = null, path, variant, onopen, onpick }: Props = $props();
	let open = $state(false);

	const active = $derived(accounts?.find((a) => a.id === current) ?? null);

	function count(a: AccountOut): number {
		return a.id === current && alerts !== null ? alerts : a.unread.warn + a.unread.error;
	}

	function statusText(a: AccountOut): string {
		switch (a.status) {
			case 'enabled':
				return a.tg.online ? 'в сети' : a.tg.user_id === null ? 'Telegram не подключён' : 'Telegram не в сети';
			case 'disabled':
				return 'выключен';
			case 'error':
				return a.status_reason ? `ошибка: ${a.status_reason}` : 'ошибка';
			case 'deleting':
				return 'удаляется';
		}
	}

	function pick() {
		open = false;
		onpick?.();
	}
</script>

{#snippet summary(a: AccountOut)}
	<span
		class="inline-block size-2 shrink-0 rounded-full {a.status === 'enabled' && a.tg.online
			? 'bg-emerald-500'
			: 'bg-zinc-500'}"
		title={statusText(a)}
		aria-hidden="true"
	></span>
	<span class="min-w-0 flex-1 truncate text-left">{accountTitle(a)}</span>
	<span class="sr-only">({statusText(a)})</span>
	{#if count(a) > 0}
		<span class="pill pill-bad" aria-label="непрочитанных предупреждений и ошибок: {count(a)}">{count(a)}</span>
	{/if}
{/snippet}

{#snippet items()}
	<ul class="flex flex-col gap-0.5" aria-label="Аккаунты">
		{#each accounts ?? [] as a (a.id)}
			<li>
				<a
					href={switchHref(path, a.id)}
					onclick={pick}
					class="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-surface-2 {a.id === current
						? 'bg-accent-soft font-medium text-fg'
						: 'text-fg-muted'}"
					aria-current={a.id === current ? 'true' : undefined}
				>
					{@render summary(a)}
				</a>
			</li>
		{/each}
		<li>
			<a href="/accounts" onclick={pick} class="block rounded-md px-3 py-2 text-sm text-accent hover:bg-surface-2"
				>Все аккаунты</a
			>
		</li>
	</ul>
{/snippet}

{#if variant === 'side'}
	<div class="px-2 pb-2">
		<button
			type="button"
			class="flex w-full items-center gap-2 rounded-md border border-line-soft px-3 py-2 text-sm hover:bg-surface-2"
			aria-expanded={open}
			onclick={() => (open = !open)}
		>
			{#if active}{@render summary(active)}{:else}<span class="flex-1 text-left text-fg-muted">Аккаунт</span>{/if}
			<ChevronsUpDown class="size-4 shrink-0 text-fg-muted" aria-hidden="true" />
		</button>
		{#if open}<div class="mt-1">{@render items()}</div>{/if}
	</div>
{:else if variant === 'bar'}
	{#if active}
		<button
			type="button"
			class="mb-3 flex w-full items-center gap-2 rounded-md border border-line-soft bg-surface px-3 py-2 text-sm md:hidden"
			aria-haspopup="dialog"
			onclick={onopen}
		>
			{@render summary(active)}
			<ChevronsUpDown class="size-4 shrink-0 text-fg-muted" aria-hidden="true" />
		</button>
	{/if}
{:else}
	<section class="mb-3 border-b border-line-soft pb-3" aria-label="Аккаунт">
		{@render items()}
	</section>
{/if}
