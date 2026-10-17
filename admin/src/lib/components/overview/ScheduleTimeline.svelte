<script lang="ts">
	import type { ScheduleOut } from '$lib/api/types';
	import {
		hourTicks,
		sharedBands,
		sharedMarks,
		sharedText
	} from '$lib/overview/schedule';
	import { fmtTime } from '$lib/util/format';
	import ScheduleRow from './ScheduleRow.svelte';

	interface Props {
		schedule: ScheduleOut;
		accountNames?: Map<number, string>;
		hours?: number;
	}

	let { schedule, accountNames = new Map(), hours = 12 }: Props = $props();

	const ticks = $derived(hourTicks(schedule.now, hours));
	const bands = $derived(sharedBands(schedule.shared, schedule.now, hours));
	const marks = $derived(sharedMarks(schedule.shared, schedule.now, hours));
	const empty = $derived(schedule.accounts.length === 0);
</script>

{#if empty}
	<section class="card space-y-2 text-sm" aria-label="Нет аккаунтов">
		<p>У вас пока нет аккаунтов. Создайте первый на странице аккаунтов.</p>
		<a href="/accounts" class="btn btn-primary">Открыть аккаунты</a>
	</section>
{:else}
	<div class="card overflow-hidden p-0 border border-line" aria-label="Шкала расписания">
		<!-- Горизонтальная прокрутка для мобильных устройств -->
		<div class="overflow-x-auto">
			<div class="min-w-[760px]">
				<!-- Шапка шкалы времени: общие отметки, заливки и засечки часов -->
				<div class="flex items-stretch border-b border-line bg-surface-2">
					<!-- Левая ячейка (закреплена) -->
					<div
						class="sticky left-0 z-20 flex w-44 shrink-0 items-center border-r border-line bg-surface-2 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-fg-muted"
					>
						Аккаунт
					</div>

					<!-- Полоса времени -->
					<div class="relative h-12 flex-1 overflow-hidden">
						<!-- Фоновые полосы общих событий (ночь, фабрика, лотерея) -->
						{#each bands as band (band.at + band.kind)}
							<div
								class="pointer-events-none absolute top-0 bottom-0 {band.kind === 'night' ? 'bg-msg-bg/25' : band.kind === 'factory' ? 'bg-warn-bg/30' : 'bg-ok-bg/25'}"
								style="left: {band.start * 100}%; width: {(band.end - band.start) * 100}%"
							></div>
						{/each}

						<!-- Линия «сейчас» -->
						<div
							class="absolute top-0 bottom-0 z-15 w-0.5 bg-bad"
							style="left: 0"
							title="Сейчас ({fmtTime(schedule.now)})"
						></div>

						<!-- Засечки часов -->
						{#each ticks as tick (tick.pos)}
							<div class="pointer-events-none absolute top-0 bottom-0 w-px bg-line" style="left: {tick.pos * 100}%"></div>
							<span
								class="pointer-events-none absolute bottom-1 -translate-x-1/2 font-mono text-[10px] text-fg-muted select-none"
								style="left: {tick.pos * 100}%"
							>
								{tick.label}
							</span>
						{/each}

						<!-- Общие точечные отметки (битвы) -->
						{#each marks as sm (sm.at + sm.kind)}
							<div
								class="absolute top-1 z-10 flex -translate-x-1/2 cursor-default items-center gap-1 rounded-sm border border-line bg-surface px-1.5 py-0.5 text-[11px] font-medium text-fg shadow-2xs"
								style="left: {sm.pos * 100}%"
								title="{sharedText(sm.kind).title} {sm.label}"
							>
								<span>{sharedText(sm.kind).icon}</span>
								<span>{sm.label}</span>
							</div>
						{/each}

						<!-- Подписи общих полос в шапке -->
						{#each bands as band (band.at + band.kind)}
							<div
								class="absolute top-1 z-5 truncate px-1 text-[10px] font-medium text-fg-muted cursor-default"
								style="left: {band.start * 100}%"
								title="{sharedText(band.kind).title} {band.label}"
							>
								<span>{sharedText(band.kind).icon} {band.label}</span>
							</div>
						{/each}
					</div>
				</div>

				<!-- Строки аккаунтов -->
				<div role="rowgroup">
					{#each schedule.accounts as account (account.id)}
						<ScheduleRow
							{account}
							name={accountNames.get(account.id) ?? `Аккаунт #${account.id}`}
							now={schedule.now}
							{hours}
							{ticks}
						/>
					{/each}
				</div>
			</div>
		</div>

		<!-- Подвал / Легенда -->
		<footer class="flex flex-wrap items-center gap-x-4 gap-y-1.5 border-t border-line bg-surface px-3 py-2 text-xs text-fg-muted">
			<span class="font-medium text-fg">Обозначения:</span>
			<span class="flex items-center gap-1">
				<span class="inline-block h-2.5 w-2.5 rounded-2xs border border-accent/40 bg-accent-soft"></span>
				Занят
			</span>
			<span class="flex items-center gap-1">
				<span class="inline-block h-2.5 w-2.5 rounded-2xs border border-msg-fg/40 bg-msg-bg"></span>
				Сон
			</span>
			<span>⚔ Битва</span>
			<span>🏭 Фабрика</span>
			<span>🤑 Лотерея</span>
			<span>🌙 Ночь</span>
			<span class="flex items-center gap-1">
				<span class="inline-block h-2.5 w-2.5 rounded-full bg-ok-bg ring-1 ring-ok"></span>
				Готово
			</span>
		</footer>
	</div>
{/if}
