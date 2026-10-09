<script lang="ts">
	import type { MetroLive } from '$lib/api/types';
	import { endText, liveMap, lostAt, modeText, STALE_AFTER_MS } from '$lib/metro/live';
	import { eventIcon, eventText, lootText, timeline } from '$lib/metro/model';
	import { accountHref } from '$lib/nav';
	import { fmtSpan, fmtTime } from '$lib/util/format';
	import Meter from '../Meter.svelte';
	import MetroMap from '../metro/MetroMap.svelte';

	interface Props {
		frame: MetroLive | null;
		/** Когда кадр получен, мс. */
		receivedAt: number | null;
		now: Date;
		account: number;
		/** По плану сейчас идёт сценарий метро. */
		metroRunning?: boolean;
		/** Идущий запуск метро по кадрам `scenario_run` (null — не видно). */
		metroRunId?: number | null;
	}
	let { frame, receivedAt, now, account, metroRunning = false, metroRunId = null }: Props = $props();

	/** Строк ленты событий. */
	const RECENT = 8;

	// Часы главной тикают раз в 30 с — для «обновлено N с назад» и времени до выброса чаще.
	let ticked = $state(Date.now());
	$effect(() => {
		if (!frame?.running) return;
		const timer = setInterval(() => (ticked = Date.now()), 5_000);
		return () => clearInterval(timer);
	});
	const at = $derived(Math.max(now.getTime(), ticked));

	// Связь с идущим забегом потеряна: время и выброс по часам страницы уже не правда.
	const lost = $derived.by(() => {
		const since = frame && receivedAt !== null ? lostAt(frame, receivedAt) : null;
		return since !== null && at >= since;
	});
	const live = $derived(frame?.running === true && !lost);
	// Виден другой запуск метро. Забег, не дошедший до конца, продолжается в том же сообщении
	// (персонаж ещё в метро) — это не новый забег; дошедший — итог прошлого. Без другого запуска
	// план мог не успеть узнать о конце только что кончившегося.
	const otherRun = $derived(metroRunId !== null && metroRunId !== frame?.scenario_run_id);
	const newRun = $derived(otherRun && frame?.outcome === 'finished');
	const resuming = $derived(otherRun && !newRun);
	const model = $derived(frame ? liveMap(frame) : null);
	const step = $derived(model ? Math.max(0, model.path.length - 1) : 0);
	const recent = $derived(model ? timeline(model).slice(-RECENT).reverse() : []);
	const found = $derived(frame ? lootText(frame.found) : '');
	// Доля бюджета — по часам страницы, а не на момент кадра: замерший кадр её не останавливает.
	const used = $derived.by(() => {
		if (!frame) return 0;
		const total = frame.budget.total_s;
		if (!frame.running || !total) return frame.budget.used;
		return Math.min(1, Math.max(0, (at - Date.parse(frame.started_at)) / 1000 / total));
	});
	const kickLeft = $derived(frame?.kick_at ? (Date.parse(frame.kick_at) - at) / 1000 : null);
	const staleS = $derived(
		live && receivedAt !== null && at - receivedAt > STALE_AFTER_MS ? (at - receivedAt) / 1000 : null
	);
</script>

<!-- Ход забега внутри «Сейчас»: показывать ли его, решает карточка (`shown`). -->
{#if frame && model}
	<div class="@container" role="group" aria-label="Метро — прохождение">
		{#if !frame.running}
			<div class="flex items-baseline justify-between gap-2">
				<p class="font-semibold">{endText(frame)}</p>
				<a class="shrink-0 text-xs text-accent hover:underline" href={accountHref(account, '/metro')}>повтор</a>
			</div>
			{#if newRun}
				<p class="text-xs text-fg-muted">Это итог прошлого забега: идёт вход в новый, карта появится с первым шагом.</p>
			{:else if resuming}
				<p class="text-xs text-fg-muted">Забег продолжается: карта обновится с первым шагом.</p>
			{:else if metroRunning}
				<p class="text-xs text-fg-muted">Последний забег.</p>
			{/if}
		{:else if lost && receivedAt !== null}
			<p class="text-sm text-fg-muted" role="status">связь потеряна, данные на {fmtTime(new Date(receivedAt))}</p>
		{/if}
		<div class="mt-2 grid gap-3 @lg:grid-cols-[minmax(0,1fr)_minmax(0,15rem)]">
			<div class="min-w-0">
				<MetroMap {model} {step} label="Карта забега: шагов {frame.steps}, посещено клеток {model.visitedCount}" />
			</div>
			<div class="min-w-0 space-y-2 text-sm">
				<p>
					шаг {frame.steps}{#if frame.pos.length === 2}{` · клетка (${frame.pos[0]},${frame.pos[1]})`}{/if}{#if frame.budget.step_s}{` · ~${frame.budget.step_s.toFixed(1)} с/шаг`}{/if}
				</p>
				<p>режим: <b>{modeText(frame)}</b></p>
				<div>
					<p>🔋 {frame.stamina === null ? '—' : `${frame.stamina}%`}</p>
					{#if frame.stamina !== null}
						<Meter value={Math.min(frame.stamina, 100)} max={100} label="Выносливость" />
					{/if}
				</div>
				{#if live && frame.budget.total_s !== null}
					<div>
						<p>
							время: {Math.round(used * 100)}%{#if kickLeft !== null}{` · ${kickLeft > 0 ? `до выброса ~${fmtSpan(kickLeft)}` : 'выброс вот-вот'}`}{/if}
						</p>
						<Meter value={Math.round(used * 100)} max={100} label="Время забега" />
					</div>
				{/if}
				<p>аптечки ❤️ {frame.packs ?? '—'}</p>
				<p><span class="text-fg-muted">Найдено</span> {found || '—'}</p>
				{#if recent.length}
					<ol class="space-y-0.5 text-xs" aria-label="Последние события">
						{#each recent as e, i (i)}
							<li class="flex gap-1.5">
								<span class="w-7 shrink-0 text-right font-mono text-fg-faint">{e.step}</span>
								<span class="w-4 shrink-0 text-center" aria-hidden="true">{eventIcon(e) ?? '•'}</span>
								<span class="ext-text min-w-0">{eventText(e)}</span>
							</li>
						{/each}
					</ol>
				{/if}
				{#if staleS !== null}
					<p class="text-xs text-warn-fg" role="status">обновлено {fmtSpan(staleS)} назад</p>
				{/if}
			</div>
		</div>
	</div>
{/if}
