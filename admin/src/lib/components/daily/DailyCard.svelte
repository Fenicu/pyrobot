<script lang="ts">
	import { errorText, type ApiError } from '$lib/api/errors';
	import type { DayOut } from '$lib/api/types';
	import { dayShort } from '$lib/daily/text';
	import { fmtTime } from '$lib/util/format';
	import DayBreakdown from './DayBreakdown.svelte';

	interface Props {
		/** Сегодняшний день ответа `/daily` (null — ещё не загружен). */
		day: DayOut | null;
		ledgerSince: string | null;
		error: ApiError | null;
		now: Date;
	}
	let { day, ledgerSince, error, now }: Props = $props();

	const short = dayShort;
</script>

<section class="card" aria-labelledby="daily-title">
	<div class="mb-2 flex items-baseline justify-between gap-2">
		<h2 id="daily-title" class="card-title mb-0">
			Итоги дня{day ? ` · ${short(day.day)} (до ${fmtTime(now)})` : ''}
		</h2>
		<a class="text-xs text-accent hover:underline" href="/daily">по дням →</a>
	</div>
	{#if day}
		<DayBreakdown
			{day}
			beforeLedger={ledgerSince === null || day.day < ledgerSince}
			ledgerSince={ledgerSince ? short(ledgerSince) : null}
		/>
		{#if error}<p class="mt-2 text-xs text-bad-fg">Не обновилось: {errorText(error)}</p>{/if}
	{:else if error}
		<p class="text-sm text-bad-fg" role="alert">Итоги недоступны: {errorText(error)}.</p>
	{:else}
		<p class="text-sm text-fg-muted">Загрузка итогов…</p>
	{/if}
</section>
