<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { DayOut } from '$lib/api/types';
	import { dayShort, staleNote } from '$lib/daily/text';
	import { fmtTime, mskDay } from '$lib/util/format';
	import DayBreakdown from './DayBreakdown.svelte';

	interface Props {
		/** Первый день ответа `/daily` (null — ещё не загружен). */
		day: DayOut | null;
		ledgerSince: string | null;
		error: ApiError | null;
		now: Date;
		/** Когда пришёл ответ (null — не известно: «до» — по `now`). */
		loadedAt?: Date | null;
	}
	let { day, ledgerSince, error, now, loadedAt = null }: Props = $props();

	const short = dayShort;
	// «Сегодня» — по текущему времени МСК: после полуночи вчерашний ответ сегодняшним не считается.
	const today = $derived(mskDay(now));
	const current = $derived(day !== null && day.day === today ? day : null);
	const note = $derived(
		day === null
			? null
			: staleNote({ today, first: day.day, loadedAt, now, error: error ? errorText(error) : null })
	);
</script>

<section class="card" aria-labelledby="daily-title">
	<div class="mb-2 flex items-baseline justify-between gap-2">
		<h2 id="daily-title" class="card-title mb-0">
			Итоги дня · {short(today)}{current ? ` (до ${fmtTime(loadedAt ?? now)})` : ''}
		</h2>
		<a class="text-xs text-accent hover:underline" href="/daily">по дням →</a>
	</div>
	{#if current}
		<div class={note ? 'opacity-60' : ''}>
			<DayBreakdown
				day={current}
				beforeLedger={ledgerSince === null || current.day < ledgerSince}
				ledgerSince={ledgerSince ? short(ledgerSince) : null}
			/>
		</div>
		{#if note}<p class="mt-2 text-xs text-bad-fg" role="status">{note}</p>{/if}
	{:else if day}
		<p class="text-sm text-fg-muted" role="status">{note}</p>
	{:else if error}
		<p class="text-sm text-bad-fg" role="alert">Итоги недоступны: {errorText(error)}.</p>
	{:else}
		<p class="text-sm text-fg-muted">Загрузка итогов…</p>
	{/if}
</section>
