<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { DailyOut, DayOut } from '$lib/api/types';
	import {
		average,
		BALANCE,
		dayLabel,
		dayShort,
		incomeCount,
		itemsCount,
		lossesMoney,
		signed,
		weekdayShort
	} from '$lib/daily/text';
	import { fmtNum, fmtTime } from '$lib/util/format';
	import DayBreakdown from './DayBreakdown.svelte';

	interface Props {
		data: DailyOut | null;
		error: ApiError | null;
		now: Date;
	}
	let { data, error, now }: Props = $props();

	const days = $derived(data?.days ?? []);
	const today = $derived(days[0]?.day ?? '');
	const since = $derived(data?.ledger_since ?? null);
	const avg = $derived(average(days));
	// Раскрытый день: по умолчанию — сегодня.
	let picked = $state<string | null>(null);
	const selected = $derived(picked ?? today);

	const short = dayShort;
	const beforeLedger = (d: DayOut) => since === null || d.day < since;
	const sinceTitle = $derived(since ? `журнал прихода с ${short(since)}` : 'журнала прихода ещё нет');
	const tone = (v: number | null | undefined) =>
		v === null || v === undefined ? 'text-fg-faint' : v < 0 ? 'text-bad-fg' : v > 0 ? 'text-ok-fg' : 'text-fg-muted';
	// Неполный — сегодня и день запуска журнала прихода; у дней до него пусты только столбцы журнала.
	const note = (d: DayOut) => (d.day === today ? `до ${fmtTime(now)}` : d.day === since ? 'неполный' : '');

	function toggle(day: string) {
		picked = selected === day ? '' : day;
	}
</script>

{#if !data}
	{#if error}
		<p class="card text-sm text-bad-fg" role="alert">Итоги недоступны: {errorText(error)}.</p>
	{:else}
		<p class="card text-sm text-fg-muted">Загрузка итогов…</p>
	{/if}
{:else}
	{#if error}<p class="mb-2 text-xs text-bad-fg">Не обновилось: {errorText(error)}</p>{/if}

	<!-- ПК: таблица за 30 дней -->
	<div class="card hidden overflow-x-auto md:block">
		<table class="w-full border-collapse text-sm tabular-nums" aria-label="Итоги по дням">
			<thead>
				<tr class="text-fg-muted">
					<th class="px-2 py-1.5 text-left font-medium">День</th>
					{#each BALANCE as b (b.key)}<th class="px-2 py-1.5 text-right font-medium" title={b.title}>{b.icon}</th>{/each}
					<th class="px-2 py-1.5 text-right font-medium">Предметы</th>
					<th class="px-2 py-1.5 text-right font-medium">Разовое</th>
					<th class="px-2 py-1.5 text-right font-medium">Потери</th>
				</tr>
			</thead>
			<tbody>
				<tr class="border-t border-line-soft text-fg-muted italic">
					<td class="px-2 py-1.5">среднее за 7 дней</td>
					{#each BALANCE as b (b.key)}
						<td class="px-2 py-1.5 text-right">{avg.balance[b.key] === null ? '—' : signed(avg.balance[b.key]!)}</td>
					{/each}
					<td class="px-2 py-1.5 text-right">{fmtNum(avg.items)}</td>
					<td class="px-2 py-1.5 text-right">{fmtNum(avg.income)}</td>
					<td class="px-2 py-1.5 text-right">{avg.losses === null ? '—' : `$${fmtNum(avg.losses)}`}</td>
				</tr>
				{#each days as d (d.day)}
					{@const open = selected === d.day}
					{@const lost = lossesMoney(d)}
					<tr class="border-t border-line-soft {open ? 'bg-accent-soft' : ''}">
						<td class="px-1 py-0.5">
							<button
								type="button"
								class="w-full rounded px-1 py-1 text-left hover:bg-surface-2"
								aria-expanded={open}
								onclick={() => toggle(d.day)}
							>
								{dayLabel(d.day, today)}
								{#if note(d)}<span class="text-fg-faint">({note(d)})</span>{/if}
							</button>
						</td>
						{#each BALANCE as b (b.key)}
							{@const delta = d.balance[b.key]?.delta ?? null}
							<td class="px-2 py-1.5 text-right {tone(delta)}" title={delta === null ? 'нет данных' : undefined}>
								{delta === null ? '—' : signed(delta)}
							</td>
						{/each}
						{#if beforeLedger(d)}
							{#each [0, 1, 2] as i (i)}<td class="px-2 py-1.5 text-right text-fg-faint" title={sinceTitle}>—</td>{/each}
						{:else}
							<td class="px-2 py-1.5 text-right">{fmtNum(itemsCount(d))}</td>
							<td class="px-2 py-1.5 text-right">{fmtNum(incomeCount(d))}</td>
							<td class="px-2 py-1.5 text-right {lost ? 'text-bad-fg' : 'text-fg-faint'}">{lost ? `$${fmtNum(lost)}` : '—'}</td>
						{/if}
					</tr>
					{#if open}
						<tr class="bg-accent-soft/40">
							<td colspan={BALANCE.length + 4} class="px-3 pt-1 pb-3">
								<section aria-label="Разбор дня {short(d.day)}">
									<DayBreakdown day={d} beforeLedger={beforeLedger(d)} ledgerSince={since ? short(since) : null} />
								</section>
							</td>
						</tr>
					{/if}
				{/each}
			</tbody>
		</table>
		{#if since}<p class="mt-2 text-xs text-fg-faint">Разовое, предметы и потери — из журнала прихода с {short(since)}.</p>{/if}
	</div>

	<!-- Телефон: карточки дней -->
	<ul class="space-y-2 md:hidden" aria-label="Дни">
		{#each days as d (d.day)}
			{@const open = selected === d.day}
			<li class="card p-2.5">
				<button type="button" class="w-full text-left" aria-expanded={open} onclick={() => toggle(d.day)}>
					<span class="mb-1 flex justify-between text-sm">
						<b>{d.day === today ? dayLabel(d.day, today) : short(d.day)}</b>
						<span class="text-fg-faint">{d.day === today ? note(d) : `${weekdayShort(d.day)}${note(d) ? ` · ${note(d)}` : ''}`}</span>
					</span>
					<span class="flex flex-wrap gap-x-3 gap-y-0.5 text-[13px]">
						{#each BALANCE as b (b.key)}
							{@const delta = d.balance[b.key]?.delta ?? null}
							<span title={b.title}>{b.icon} <b class={tone(delta)}>{delta === null ? '—' : signed(delta)}</b></span>
						{/each}
					</span>
					<span class="mt-0.5 block text-xs text-fg-faint">
						{#if beforeLedger(d)}
							разовое — нет данных{since ? ` до ${short(since)}` : ''}
						{:else}
							разовое: {incomeCount(d)} · потери: {lossesMoney(d) ? `$${fmtNum(lossesMoney(d))}` : '—'}
						{/if}
					</span>
				</button>
				{#if open}
					<section class="mt-2 border-t border-line-soft pt-2" aria-label="Разбор дня {short(d.day)}">
						<DayBreakdown day={d} beforeLedger={beforeLedger(d)} ledgerSince={since ? short(since) : null} />
					</section>
				{/if}
			</li>
		{/each}
	</ul>
{/if}
