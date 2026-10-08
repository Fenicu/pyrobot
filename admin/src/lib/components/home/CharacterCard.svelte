<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import type { DayOut, PublicState } from '$lib/api/types';
	import { levelForecast, levelForecastText } from '$lib/daily/forecast';
	import { fmtCompact, fmtNum, fmtRelative } from '$lib/util/format';
	import { busyText, COMPANY } from '$lib/util/game';
	import { val } from '$lib/util/observed';
	import TangerinePartner from '../TangerinePartner.svelte';
	import Row from './Row.svelte';

	interface Props {
		state: PublicState;
		stale: string[];
		now: Date;
		/** Дни «Итогов дня»: по ним считается темп опыта для прогноза уровня. */
		days?: DayOut[];
		/** Клиент аккаунта: под запасом 🍊 — кому аккаунт их дарит. */
		api?: AccountApi;
		/** Версия настроек: сменилась — адресат 🍊 перечитывается. */
		settingsVersion?: number | null;
	}
	let { state, stale, now, days = [], api, settingsVersion = null }: Props = $props();

	const level = $derived(val(state, 'level'));
	const exp = $derived(val(state, 'exp'));
	const expNext = $derived(val(state, 'exp_next'));
	// Профиль даёт весь опыт и порог следующего уровня, а не начало текущего: доля «опыт / порог»
	// почти всегда около 100% и ничего не говорит — показывается, сколько осталось.
	const expLeft = $derived(exp !== null && expNext !== null ? expNext - exp : null);
	const forecast = $derived(levelForecast(days, expLeft, now));
	const motivation = $derived(val(state, 'motivation'));
	const motivationMax = $derived(val(state, 'motivation_max'));
	const nextAt = $derived(val(state, 'motivation_next_at'));
	const busy = $derived(val(state, 'busy'));
	const company = $derived(val(state, 'company'));
	const teamTag = $derived(val(state, 'team_tag'));
	const isStale = (field: string) => stale.includes(field);

	// Стартап: уровень и прогресс к следующему уровню; `max` — потолок игры, прогресса нет.
	const startup = $derived(val(state, 'startup'));
	const startupLevel = $derived(startup?.level);
	const startupMax = $derived(startup?.max === true);
	const startupProgress = $derived(startup?.progress);
	const startupNeeded = $derived(startup?.progress_needed);
	const startupText = $derived(
		[
			typeof startupLevel === 'number' ? `ур. ${startupLevel}` : '',
			startupMax
				? 'максимальный'
				: typeof startupProgress === 'number'
					? `прогресс ${fmtNum(startupProgress)} из ${typeof startupNeeded === 'number' ? fmtNum(startupNeeded) : '?'}`
					: ''
		].filter(Boolean).join(' · ')
	);
</script>

<section class="card" aria-labelledby="character-title">
	<h2 id="character-title" class="card-title">Персонаж{level !== null ? ` · ур. ${level}` : ''}</h2>
	{#if Object.keys(state).length === 0}
		<p class="text-sm text-fg-muted">Снимка ещё нет: бот не видел ни одного экрана.</p>
	{:else}
		{#if company}
			<Row label="Компания" stale={isStale('company')}>
				{COMPANY[company] ? `${COMPANY[company].mark} ${COMPANY[company].name}` : company}
			</Row>
		{/if}
		{#if teamTag}
			<Row label="Команда" stale={isStale('team_tag')}>[{teamTag}]</Row>
		{/if}
		<Row label="💡 опыт" stale={isStale('exp')}>{fmtCompact(exp)}</Row>
		{#if expLeft !== null && level !== null}
			<Row label="до ур. {level + 1}" stale={isStale('exp')}>
				{expLeft > 0 ? `${fmtNum(expLeft)} 💡` : 'набран — ждёт повышения'}{#if forecast}{` · ${levelForecastText(forecast)}`}{/if}
			</Row>
		{/if}
		<Row label="💵 деньги" stale={isStale('money')}>${fmtNum(val(state, 'money'))}</Row>
		<Row label="🔥 мотивация" stale={isStale('motivation')}>
			{fmtNum(motivation)} / {fmtNum(motivationMax)}
			{#if motivation !== null && motivationMax !== null && motivation >= motivationMax}
				· полная
			{:else if nextAt}
				· +1 {fmtRelative(nextAt, now)}
			{/if}
		</Row>
		<Row label="🔋 выносливость" stale={isStale('stamina')}>{fmtNum(val(state, 'stamina'))}</Row>
		<Row label="🍊 мандарины" stale={isStale('tangerines')}>{fmtNum(val(state, 'tangerines'))}</Row>
		{#if api}<div class="text-right"><TangerinePartner {api} refresh={settingsVersion} /></div>{/if}
		<Row label="📚 🔩 ⚙️" stale={isStale('knowledge') || isStale('raw') || isStale('details')}>
			{fmtNum(val(state, 'knowledge'))} · {fmtNum(val(state, 'raw'))} · {fmtNum(val(state, 'details'))}
		</Row>
		{#if startup}
			<Row label="🔮 Стартап" stale={isStale('startup')}>{startupText || '—'}</Row>
		{/if}
		<Row label="Занятость" stale={isStale('busy')}>
			{#if busy}
				{busyText(busy, now)}
			{:else}
				свободен
			{/if}
		</Row>
	{/if}
</section>
