<script lang="ts">
	import { onMount } from 'svelte';
	import { accounts, api } from '$lib/app.svelte';
	import { ApiFailure, errorText } from '$lib/api/errors';
	import type { SettingsBulkIn } from '$lib/api/types';
	import AccountsPending from '$lib/components/AccountsPending.svelte';
	import BulkBar from '$lib/components/overview/BulkBar.svelte';
	import BulkResultsDialog from '$lib/components/overview/BulkResultsDialog.svelte';
	import BulkSettingModal from '$lib/components/overview/BulkSettingModal.svelte';
	import SummaryCards from '$lib/components/overview/SummaryCards.svelte';
	import SummaryTable from '$lib/components/overview/SummaryTable.svelte';
	import { OverviewAccountsStore } from '$lib/overview/accounts.svelte';
	import {
		battleOutlook,
		changeSettings,
		pauseAll,
		pauseWarning,
		summarizeResults,
		type BulkSummary,
		type Selection
	} from '$lib/overview/bulk';
	import { loadSort, mergeRows, nextSort, saveSort, sortRows, type SortKey } from '$lib/overview/table';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import { accountTitle } from '$lib/util/game';

	const store = new OverviewAccountsStore(api);
	let sort = $state(loadSort());
	// «до 14:40» и «снимок от …» кончаются между опросами: часы страницы.
	let now = $state(new Date());

	const list = $derived(accounts.list);
	const rows = $derived(list === null ? [] : sortRows(mergeRows(list, store.data ?? []), sort.key, sort.dir));
	const waiting = $derived(store.data === null && store.error === null);

	// Выбор строк для массовых действий; исчезнувшие и удаляемые аккаунты из него выпадают.
	let selected = $state<Set<number>>(new Set());
	const pickable = $derived(rows.filter((r) => r.account.status !== 'deleting').map((r) => r.account.id));
	const chosen = $derived(pickable.filter((id) => selected.has(id)));
	const selection: Selection = {
		get selected() {
			return selected;
		},
		toggle(id) {
			const next = new Set(selected);
			if (!next.delete(id)) next.add(id);
			selected = next;
		},
		toggleAll(on) {
			selected = on ? new Set(pickable) : new Set();
		}
	};

	let busy = $state(false);
	let settingsOpen = $state(false);
	let report = $state<{ title: string; summary: BulkSummary } | null>(null);

	const nameOf = (id: number) => {
		const a = list?.find((x) => x.id === id);
		return a ? accountTitle(a) : `#${id}`;
	};

	async function reload() {
		await Promise.all([store.load(), accounts.load()]);
	}

	function warning(): string | null {
		const outlook = battleOutlook(rows);
		return pauseWarning(now, outlook.at, outlook.needsTarget);
	}

	async function runEngine(action: 'pause' | 'resume', ids: number[] | null) {
		busy = true;
		try {
			const out = await pauseAll(api, action, ids);
			report = {
				title: action === 'pause' ? 'Пауза: результат' : 'Продолжение: результат',
				summary: summarizeResults(out.results)
			};
			await reload();
		} catch (e) {
			toasts.show(e instanceof ApiFailure ? errorText(e.error) : String(e), 'error');
		} finally {
			busy = false;
		}
	}

	async function engineAll(action: 'pause' | 'resume') {
		const pause = action === 'pause';
		const warn = pause ? warning() : null;
		const ok = await dialogs.confirm({
			title: pause ? 'Поставить на паузу все аккаунты?' : 'Продолжить все аккаунты?',
			body: [
				pause
					? 'Бот перестанет принимать решения на всех запущенных аккаунтах.'
					: 'Бот снова начнёт принимать решения на всех запущенных аккаунтах.',
				warn
			]
				.filter(Boolean)
				.join(' '),
			confirmText: pause ? 'Пауза всех' : 'Продолжить всех',
			danger: pause && warn !== null
		});
		if (ok) await runEngine(action, null);
	}

	async function engineChosen(action: 'pause' | 'resume') {
		const warn = action === 'pause' ? warning() : null;
		if (warn !== null) {
			const ok = await dialogs.confirm({
				title: `Поставить на паузу выбранные (${chosen.length})?`,
				body: warn,
				confirmText: 'Пауза',
				danger: true
			});
			if (!ok) return;
		}
		await runEngine(action, chosen);
	}

	async function applySettings(body: SettingsBulkIn) {
		const out = await changeSettings(api, body);
		settingsOpen = false;
		report = { title: 'Настройка: результат', summary: summarizeResults(out.results) };
		await reload();
	}

	function onsort(key: SortKey) {
		sort = nextSort(sort, key);
		saveSort(sort);
	}

	// Опрос живёт, пока открыт экран.
	onMount(() => {
		store.start();
		const t = setInterval(() => (now = new Date()), 30_000);
		return () => {
			clearInterval(t);
			store.stop();
		};
	});
</script>

<svelte:head><title>Обзор · pyrobot</title></svelte:head>

{#if list === null}
	<AccountsPending error={accounts.error} onretry={() => void accounts.load()} />
{:else if list.length === 0}
	<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
		<p>Аккаунтов пока нет.</p>
		<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
	</section>
{:else if waiting}
	<p class="card text-sm text-fg-muted" role="status">Загрузка…</p>
{:else}
	<div class="space-y-3">
		{#if store.error}
			<p class="text-sm text-bad-fg" role="alert">
				Сводка недоступна: {errorText(store.error)}.{store.data !== null ? ' Показаны прежние данные.' : ''}
			</p>
		{/if}
		<div class="flex flex-wrap justify-end gap-2">
			<button type="button" class="btn" disabled={busy} onclick={() => void engineAll('pause')}>Пауза всех</button>
			<button type="button" class="btn" disabled={busy} onclick={() => void engineAll('resume')}>Продолжить всех</button>
		</div>
		<div class="hidden md:block">
			<SummaryTable {rows} {now} {sort} {onsort} {selection} />
		</div>
		<div class="md:hidden">
			<SummaryCards {rows} {now} {selection} />
		</div>
		{#if chosen.length > 0}
			<BulkBar
				count={chosen.length}
				{busy}
				onpause={() => void engineChosen('pause')}
				onresume={() => void engineChosen('resume')}
				onsettings={() => (settingsOpen = true)}
				onclear={() => selection.toggleAll(false)}
			/>
		{/if}
	</div>
	{#if settingsOpen}
		<BulkSettingModal {api} {rows} initial={chosen} onapply={applySettings} onclose={() => (settingsOpen = false)} />
	{/if}
	{#if report}
		<BulkResultsDialog title={report.title} summary={report.summary} {nameOf} onclose={() => (report = null)} />
	{/if}
{/if}
