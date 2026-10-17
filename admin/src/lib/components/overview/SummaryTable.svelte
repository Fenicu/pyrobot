<script lang="ts">
	import { goto } from '$app/navigation';
	import {
		ACTIVITY_COLOR,
		accountActivity,
		accountDotLabel,
		accountTone,
		tgOffline,
		tgUnreachable
	} from '$lib/accounts/status';
	import { accountHref } from '$lib/nav';
	import { readyText } from '$lib/plan/text';
	import {
		artifactText,
		artifactTitle,
		metroText,
		nextActionText,
		skillsText,
		totalsRow,
		type OverviewRow,
		type SortKey,
		type SortState
	} from '$lib/overview/table';
	import { fmtNum, fmtTime } from '$lib/util/format';
	import { accountTitle } from '$lib/util/game';
	import Meter from '../Meter.svelte';
	import StatusDot from '../ui/StatusDot.svelte';
	import BattleCell from './BattleCell.svelte';

	interface Props {
		rows: OverviewRow[];
		now: Date;
		sort: SortState;
		onsort: (key: SortKey) => void;
	}
	let { rows, now, sort, onsort }: Props = $props();

	const totals = $derived(totalsRow(rows));

	/** Колонки с сортировкой; остальные (навыки, артефакт, метро, письма) - только чтение. */
	const COLUMNS: { key: SortKey | null; label: string; title: string; align?: 'right' }[] = [
		{ key: 'name', label: 'Аккаунт', title: 'Аккаунт' },
		{ key: 'level', label: '🎚', title: 'Уровень' },
		{ key: 'expShare', label: '💡', title: 'Доля опыта до следующего уровня' },
		{ key: 'money', label: '💵', title: 'Деньги', align: 'right' },
		{ key: 'stamina', label: '🔋', title: 'Энергия', align: 'right' },
		{ key: 'motivation', label: '🔥', title: 'Мотивация', align: 'right' },
		{ key: null, label: '📚', title: 'Знания', align: 'right' },
		{ key: null, label: '🔩', title: 'Сырьё', align: 'right' },
		{ key: null, label: '⚙️', title: 'Детали', align: 'right' },
		{ key: null, label: 'Навыки', title: 'Навыки' },
		{ key: 'busyUntil', label: 'Занят', title: 'Чем занят и до какого времени' },
		{ key: null, label: 'Бот', title: 'Следующее действие бота' },
		{ key: 'battleReady', label: 'Цель на битву', title: 'Цель на ближайшую битву' },
		{ key: null, label: '👾', title: 'Сбор артефакта' },
		{ key: null, label: '🚇', title: 'Метро' },
		{ key: 'problems', label: 'Внимание', title: 'Непрочитанное и проблемы' }
	];

	function ariaSort(key: SortKey | null): 'ascending' | 'descending' | 'none' | undefined {
		if (key === null) return undefined;
		return sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none';
	}

	function open(e: MouseEvent, row: OverviewRow) {
		if (row.account.status === 'deleting') return;
		if ((e.target as Element).closest('a, button, input, select, [data-no-row-click]')) return;
		void goto(accountHref(row.account.id, ''));
	}

	const hm = (value: string | null) => (value ? fmtTime(value) : '');
</script>

<div class="overflow-x-auto rounded-card border border-line bg-surface">
	<table class="w-full min-w-[78rem] border-collapse text-sm">
		<caption class="sr-only">Сводка по аккаунтам</caption>
		<thead>
			<tr class="border-b border-line text-left text-xs text-fg-muted">
				{#each COLUMNS as col (col.label)}
					<th
						scope="col"
						class="px-2.5 py-2 font-medium whitespace-nowrap {col.align === 'right' ? 'text-right' : ''}"
						aria-sort={ariaSort(col.key)}
						title={col.title}
					>
						{#if col.key !== null}
							{@const key = col.key}
							<button type="button" class="inline-flex items-center gap-1 hover:text-fg" onclick={() => onsort(key)}>
								{col.label}
								<span aria-hidden="true" class="text-fg-faint">{sort.key === key ? (sort.dir === 'asc' ? '▲' : '▼') : ''}</span>
								<span class="sr-only">: {col.title}</span>
							</button>
						{:else}
							{col.label}
						{/if}
					</th>
				{/each}
			</tr>
		</thead>
		<tbody>
			{#each rows as row (row.account.id)}
				{@const a = row.account}
				{@const x = row.extra}
				{@const act = accountActivity(a, now)}
				{@const next = x?.next_action ? nextActionText(x.next_action) : null}
				{@const stale = x?.stale ?? false}
				{@const deleting = a.status === 'deleting'}
				<!-- Строка целиком кликабельна для мыши; с клавиатуры тот же переход даёт ссылка в имени. -->
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
				<tr
					class="border-b border-line-soft align-top last:border-0 {deleting ? 'text-fg-faint' : 'cursor-pointer hover:bg-surface-2/60'}"
					data-stale={stale}
					onclick={(e) => open(e, row)}
				>
					<td class="px-2.5 py-2">
						<div class="flex items-center gap-2">
							<StatusDot tone={accountTone(a)} label={accountDotLabel(a)} />
							{#if deleting}
								<span class="ext-text min-w-0 font-medium">{accountTitle(a)}</span>
							{:else}
								<a href={accountHref(a.id, '')} class="ext-text min-w-0 font-medium hover:underline">{accountTitle(a)}</a>
							{/if}
							{#if tgOffline(a)}<span class="text-warn-fg" title="Telegram не в сети">⚠</span>{/if}
							{#if tgUnreachable(a)}<span class="text-warn-fg" title="Нет связи с Telegram">⚠</span>{/if}
						</div>
						{#if stale && x?.seen_at}<p class="text-xs text-fg-faint">снимок от {fmtTime(x.seen_at)}</p>{/if}
					</td>
					<td class="px-2.5 py-2 tabular-nums {stale ? 'opacity-55' : ''}">{a.level ?? '—'}</td>
					<td class="min-w-24 px-2.5 py-2 tabular-nums {stale ? 'opacity-55' : ''}">
						{#if x?.exp != null && x.exp_next}
							<Meter value={x.exp} max={x.exp_next} label="Опыт аккаунта {a.name}" />
							<span class="text-xs text-fg-muted">{fmtNum(x.exp)} / {fmtNum(x.exp_next)}</span>
						{:else}—{/if}
					</td>
					<td class="px-2.5 py-2 text-right tabular-nums {stale ? 'opacity-55' : ''}">{fmtNum(x?.money)}</td>
					<td class="px-2.5 py-2 text-right tabular-nums {stale ? 'opacity-55' : ''}">{fmtNum(x?.stamina)}</td>
					<td class="px-2.5 py-2 text-right tabular-nums {stale ? 'opacity-55' : ''}">
						{x?.motivation != null ? `${x.motivation}/${x.motivation_max ?? '?'}` : '—'}
					</td>
					<td class="px-2.5 py-2 text-right tabular-nums {stale ? 'opacity-55' : ''}">{fmtNum(x?.knowledge)}</td>
					<td class="px-2.5 py-2 text-right tabular-nums {stale ? 'opacity-55' : ''}">{fmtNum(x?.raw)}</td>
					<td class="px-2.5 py-2 text-right tabular-nums {stale ? 'opacity-55' : ''}">{fmtNum(x?.details)}</td>
					<td class="px-2.5 py-2 whitespace-nowrap tabular-nums {stale ? 'opacity-55' : ''}">{skillsText(x)}</td>
					<td class="px-2.5 py-2" data-testid="activity">
						<span class={ACTIVITY_COLOR[act.tone]}>{act.text}</span>
						{#if act.until}<span class="text-fg-muted tabular-nums"> до {fmtTime(act.until)}</span>{/if}
					</td>
					<td class="px-2.5 py-2" data-testid="bot">
						{#if next}
							<span>{next.text}</span>
							{#if next.at}<span class="text-fg-muted tabular-nums"> в {hm(next.at)}</span>{/if}
							{#if x?.next_action?.blocked_by}
								<span class="block text-xs text-warn-fg">{readyText(x.next_action.blocked_by)}</span>
							{/if}
						{:else}
							<span class="text-fg-faint">—</span>
						{/if}
					</td>
					<td class="px-2.5 py-2"><BattleCell {row} /></td>
					<td class="px-2.5 py-2 whitespace-nowrap {stale ? 'opacity-55' : ''}" title={artifactTitle(x)}>{artifactText(x, now)}</td>
					<td class="px-2.5 py-2 whitespace-nowrap {stale ? 'opacity-55' : ''}">{metroText(row, now)}</td>
					<td class="px-2.5 py-2 whitespace-nowrap">
						{#if a.unread.error > 0}<span class="pill pill-bad" title="Ошибки">{a.unread.error}</span>{/if}
						{#if a.unread.warn > 0}<span class="pill pill-warn" title="Предупреждения">{a.unread.warn}</span>{/if}
						{#if a.unread.error === 0 && a.unread.warn === 0}<span class="text-fg-faint">—</span>{/if}
					</td>
				</tr>
			{/each}
		</tbody>
		<tfoot>
			<tr class="border-t border-line font-medium">
				<th scope="row" class="px-2.5 py-2 text-left" colspan="3">Всего</th>
				<td class="px-2.5 py-2 text-right tabular-nums">{fmtNum(totals.money)}</td>
				<td colspan="2"></td>
				<td class="px-2.5 py-2 text-right tabular-nums">{fmtNum(totals.knowledge)}</td>
				<td class="px-2.5 py-2 text-right tabular-nums">{fmtNum(totals.raw)}</td>
				<td class="px-2.5 py-2 text-right tabular-nums">{fmtNum(totals.details)}</td>
				<td colspan="7"></td>
			</tr>
		</tfoot>
	</table>
</div>
