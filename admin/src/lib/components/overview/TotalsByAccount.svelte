<script lang="ts">
	import type { AccountOut, OverviewTotals, TotalsAccount } from '$lib/api/types';
	import { moneySigned } from '$lib/daily/text';
	import { accountHref } from '$lib/nav';
	import { formatDelta, levelsText, METRICS } from '$lib/overview/totals';
	import { fmtNum } from '$lib/util/format';
	import { accountTitle } from '$lib/util/game';

	interface Props {
		data: OverviewTotals;
		/** Список аккаунтов за именами; null - ещё не пришёл (подпись «#id»). */
		accounts: AccountOut[] | null;
	}
	let { data, accounts }: Props = $props();

	const title = (id: number) => {
		const a = accounts?.find((x) => x.id === id);
		return a ? accountTitle(a) : `#${id}`;
	};
	const items = (a: TotalsAccount) => Object.values(a.totals.items).reduce((s, n) => s + n, 0);
	const lost = (a: TotalsAccount) => -a.totals.losses.reduce((s, k) => s + (k.amounts.money ?? 0), 0);
	// Журнала прихода ещё не было: разовое и потери неизвестны, а не нулевые.
	const noLedger = (a: TotalsAccount) => a.ledger_since === null;
	const tone = (d: number | null) => (d === null ? 'text-fg-faint' : d < 0 ? 'text-bad-fg' : d > 0 ? 'text-ok-fg' : 'text-fg-muted');
	const href = (id: number) => accountHref(id, '/daily');
</script>

<div class="hidden overflow-x-auto rounded-card border border-line bg-surface md:block">
	<table class="w-full border-collapse text-sm tabular-nums" aria-label="По аккаунтам">
		<thead>
			<tr class="border-b border-line text-left text-xs text-fg-muted">
				<th scope="col" class="px-2.5 py-2 font-medium">Аккаунт</th>
				{#each METRICS as m (m.key)}
					<th scope="col" class="px-2.5 py-2 text-right font-medium whitespace-nowrap" title={m.title}>
						<span aria-hidden="true">{m.icon}</span><span class="sr-only">{m.title}</span>
					</th>
				{/each}
				<th scope="col" class="px-2.5 py-2 text-right font-medium whitespace-nowrap" title="Повышения уровня">🎚</th>
				<th scope="col" class="px-2.5 py-2 text-right font-medium whitespace-nowrap" title="Трофеи за задания">🏆 задания</th>
				<th scope="col" class="px-2.5 py-2 text-right font-medium whitespace-nowrap">Предметы</th>
				<th scope="col" class="px-2.5 py-2 text-right font-medium whitespace-nowrap">Потери</th>
			</tr>
		</thead>
		<tbody>
			{#each data.accounts as a (a.id)}
				<tr class="border-b border-line-soft last:border-0">
					<td class="px-2.5 py-2"><a href={href(a.id)} class="ext-text font-medium hover:underline">{title(a.id)}</a></td>
					{#each METRICS as m (m.key)}
						{@const d = a.totals.balance[m.key]!}
						<td class="px-2.5 py-2 text-right whitespace-nowrap {tone(d.delta)}">{formatDelta(d, m.key, 1)}</td>
					{/each}
					<td class="px-2.5 py-2 text-right">{levelsText(a.totals.levels)}</td>
					{#if noLedger(a)}
						{#each [0, 1, 2] as i (i)}<td class="px-2.5 py-2 text-right text-fg-faint" title="журнала прихода ещё нет">—</td>{/each}
					{:else}
						<td class="px-2.5 py-2 text-right">{fmtNum(a.totals.trophies)}</td>
						<td class="px-2.5 py-2 text-right">{fmtNum(items(a))}</td>
						<td class="px-2.5 py-2 text-right {lost(a) ? 'text-bad-fg' : 'text-fg-faint'}">{lost(a) ? moneySigned(-lost(a)) : '—'}</td>
					{/if}
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<ul class="space-y-2.5 md:hidden" aria-label="Аккаунты">
	{#each data.accounts as a (a.id)}
		<li class="rounded-card border border-line bg-surface p-3 text-sm">
			<a href={href(a.id)} class="ext-text block font-medium">{title(a.id)}</a>
			<ul class="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1 tabular-nums" aria-label="Изменение за период">
				{#each METRICS as m (m.key)}
					{@const d = a.totals.balance[m.key]!}
					<li title={m.title}>
						<span aria-hidden="true">{m.icon}</span><span class="sr-only">{m.title}</span>
						<span class={tone(d.delta)}>{formatDelta(d, m.key, 1)}</span>
					</li>
				{/each}
				<li>🎚 {levelsText(a.totals.levels)}</li>
				{#if !noLedger(a)}
					<li>🏆 задания {fmtNum(a.totals.trophies)}</li>
					<li>Предметы {fmtNum(items(a))}</li>
					<li class={lost(a) ? 'text-bad-fg' : 'text-fg-faint'}>Потери {lost(a) ? moneySigned(-lost(a)) : '—'}</li>
				{/if}
			</ul>
		</li>
	{/each}
</ul>
