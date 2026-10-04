<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();

	// Retention
	let messagesDays = $state<number>(90);
	let decisionsDays = $state<number>(30);
	let metricsDays = $state<number>(365);
	let ledgerDays = $state<number>(31);
	let auditDays = $state<number>(365);

	// Invites
	let defaultTtlH = $state<number>(72);
	let defaultMaxAccounts = $state<number>(1);

	// Limits
	let maxAccountsTotal = $state<number>(50);
	let ssePerUser = $state<number>(5);
	let tgCodesPerHour = $state<number>(10);
	let tgCodesPerAccountHour = $state<number>(3);

	// Engine bounds
	let minRequestIntervalSMin = $state<number>(1.6);
	let antifloodPauseSMin = $state<number>(10.0);
	let antifloodRetryMaxMax = $state<number>(2);
	let actionTtlSMax = $state<number>(600.0);

	let saveBusy = $state(false);
	let validationError = $state('');

	function syncFromStore() {
		const v = store.serverSettings?.values as Record<string, Record<string, unknown>> | undefined;
		if (!v) return;

		const ret = v.retention ?? {};
		messagesDays = Number(ret.messages_days ?? 90);
		decisionsDays = Number(ret.decisions_days ?? 30);
		metricsDays = Number(ret.metrics_days ?? 365);
		ledgerDays = Number(ret.ledger_days ?? 31);
		auditDays = Number(ret.audit_days ?? 365);

		const inv = v.invites ?? {};
		defaultTtlH = Number(inv.default_ttl_h ?? 72);
		defaultMaxAccounts = Number(inv.default_max_accounts ?? 1);

		const lim = v.limits ?? {};
		maxAccountsTotal = Number(lim.max_accounts_total ?? 50);
		ssePerUser = Number(lim.sse_per_user ?? 5);
		tgCodesPerHour = Number(lim.tg_codes_per_hour ?? 10);
		tgCodesPerAccountHour = Number(lim.tg_codes_per_account_hour ?? 3);

		const eb = v.engine_bounds ?? {};
		minRequestIntervalSMin = Number(eb.min_request_interval_s_min ?? 1.6);
		antifloodPauseSMin = Number(eb.antiflood_pause_s_min ?? 10.0);
		antifloodRetryMaxMax = Number(eb.antiflood_retry_max_max ?? 2);
		actionTtlSMax = Number(eb.action_ttl_s_max ?? 600.0);
	}

	$effect(() => {
		if (store.serverSettings) {
			syncFromStore();
		}
	});

	onMount(() => {
		if (!store.serverSettings) {
			void store.loadServerSettings();
		}
	});

	async function reload() {
		await store.loadServerSettings();
		syncFromStore();
	}

	async function save(e: SubmitEvent) {
		e.preventDefault();
		if (!store.serverSettings) return;

		validationError = '';

		// Validate bounds
		if (messagesDays < 1 || messagesDays > 3650) {
			validationError = 'Хранение сообщений должно быть от 1 до 3650 дней';
			return;
		}
		if (decisionsDays < 1 || decisionsDays > 3650) {
			validationError = 'Хранение решений должно быть от 1 до 3650 дней';
			return;
		}
		if (metricsDays < 1 || metricsDays > 3650) {
			validationError = 'Хранение метрик должно быть от 1 до 3650 дней';
			return;
		}
		if (ledgerDays < 31 || ledgerDays > 3650) {
			validationError = 'Хранение прихода (ledger) должно быть от 31 до 3650 дней';
			return;
		}
		if (auditDays < 1 || auditDays > 3650) {
			validationError = 'Хранение журнала действий должно быть от 1 до 3650 дней';
			return;
		}

		if (defaultTtlH < 1 || defaultTtlH > 720) {
			validationError = 'Срок приглашений по умолчанию должен быть от 1 до 720 часов';
			return;
		}
		if (defaultMaxAccounts < 1 || defaultMaxAccounts > 1000) {
			validationError = 'Лимит аккаунтов по умолчанию должен быть от 1 до 1000';
			return;
		}

		if (maxAccountsTotal < 1 || maxAccountsTotal > 10000) {
			validationError = 'Максимум аккаунтов на сервере должен быть от 1 до 10000';
			return;
		}
		if (ssePerUser < 1 || ssePerUser > 100) {
			validationError = 'SSE-подключений на пользователя должно быть от 1 до 100';
			return;
		}
		if (tgCodesPerHour < 1) {
			validationError = 'Кодов Telegram в час на хост должно быть не меньше 1';
			return;
		}
		if (tgCodesPerAccountHour < 1) {
			validationError = 'Кодов Telegram в час на аккаунт должно быть не меньше 1';
			return;
		}

		if (minRequestIntervalSMin < 0 || minRequestIntervalSMin > 60) {
			validationError = 'Мин. интервал между запросами должен быть от 0 до 60 с';
			return;
		}
		if (antifloodPauseSMin < 0 || antifloodPauseSMin > 600) {
			validationError = 'Мин. пауза антифлуда должна быть от 0 до 600 с';
			return;
		}
		if (antifloodRetryMaxMax < 0) {
			validationError = 'Макс. число повторов антифлуда должно быть >= 0';
			return;
		}
		if (actionTtlSMax <= 0 || actionTtlSMax > 3600) {
			validationError = 'Макс. срок действия должен быть от 0.1 до 3600 с';
			return;
		}

		const changes = {
			retention: {
				messages_days: messagesDays,
				decisions_days: decisionsDays,
				metrics_days: metricsDays,
				ledger_days: ledgerDays,
				audit_days: auditDays
			},
			invites: {
				default_ttl_h: defaultTtlH,
				default_max_accounts: defaultMaxAccounts
			},
			limits: {
				max_accounts_total: maxAccountsTotal,
				sse_per_user: ssePerUser,
				tg_codes_per_hour: tgCodesPerHour,
				tg_codes_per_account_hour: tgCodesPerAccountHour
			},
			engine_bounds: {
				min_request_interval_s_min: minRequestIntervalSMin,
				antiflood_pause_s_min: antifloodPauseSMin,
				antiflood_retry_max_max: antifloodRetryMaxMax,
				action_ttl_s_max: actionTtlSMax
			}
		};

		saveBusy = true;
		try {
			await store.patchServerSettings(store.serverSettings.version, changes);
			toasts.show('Настройки сервера сохранены', 'ok');
		} catch {
			// error displayed in banner
		} finally {
			saveBusy = false;
		}
	}
</script>

<div class="space-y-4">
	{#if store.conflictVersion !== null || store.serverError === 'Настройки изменились, перечитать'}
		<div class="card flex flex-wrap items-center justify-between gap-3 border-warn-bg bg-accent-soft p-4" role="alert">
			<span class="text-sm font-medium text-fg">
				Настройки изменились, перечитать
				{#if store.conflictVersion !== null}
					(версия на сервере: {store.conflictVersion})
				{/if}
			</span>
			<button type="button" class="btn btn-primary" onclick={reload}>
				Перечитать
			</button>
		</div>
	{:else if store.serverError}
		<p class="card text-sm text-bad-fg" role="alert">{store.serverError}</p>
	{/if}

	{#if validationError}
		<p class="card text-sm text-bad-fg" role="alert">{validationError}</p>
	{/if}

	{#if store.serverLoading && !store.serverSettings}
		<p class="text-sm text-fg-muted" role="status">Загрузка настроек сервера…</p>
	{:else if store.serverSettings}
		<form onsubmit={save} novalidate class="space-y-6">
			<!-- Retention -->
			<div class="card space-y-3">
				<h2 class="text-sm font-semibold text-fg">Сроки хранения данных (Retention)</h2>
				<p class="text-xs text-fg-muted">Политика очистки старых данных сервера и аккаунтов (в днях).</p>
				<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
					<label class="block space-y-1">
						<span class="label">Сообщения (1..3650 дн)</span>
						<input type="number" class="input" bind:value={messagesDays} min="1" max="3650" required />
					</label>
					<label class="block space-y-1">
						<span class="label">Решения (1..3650 дн)</span>
						<input type="number" class="input" bind:value={decisionsDays} min="1" max="3650" required />
					</label>
					<label class="block space-y-1">
						<span class="label">Метрики (1..3650 дн)</span>
						<input type="number" class="input" bind:value={metricsDays} min="1" max="3650" required />
					</label>
					<label class="block space-y-1">
						<span class="label">Приход / леджер (31..3650 дн)</span>
						<input type="number" class="input" bind:value={ledgerDays} min="31" max="3650" required />
					</label>
					<label class="block space-y-1">
						<span class="label">Журнал действий / аудит (1..3650 дн)</span>
						<input type="number" class="input" bind:value={auditDays} min="1" max="3650" required />
					</label>
				</div>
			</div>

			<!-- Invites defaults -->
			<div class="card space-y-3">
				<h2 class="text-sm font-semibold text-fg">Приглашения по умолчанию</h2>
				<p class="text-xs text-fg-muted">Значения по умолчанию для создаваемых приглашений.</p>
				<div class="grid gap-3 sm:grid-cols-2">
					<label class="block space-y-1">
						<span class="label">Срок действия по умолчанию (1..720 ч)</span>
						<input type="number" class="input" bind:value={defaultTtlH} min="1" max="720" required />
					</label>
					<label class="block space-y-1">
						<span class="label">Лимит аккаунтов по умолчанию (1..1000)</span>
						<input type="number" class="input" bind:value={defaultMaxAccounts} min="1" max="1000" required />
					</label>
				</div>
			</div>

			<!-- Limits -->
			<div class="card space-y-3">
				<h2 class="text-sm font-semibold text-fg">Ограничения сервера (Limits)</h2>
				<p class="text-xs text-fg-muted">Лимиты емкости и запросов Telegram.</p>
				<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
					<label class="block space-y-1">
						<span class="label">Всего аккаунтов (1..10000)</span>
						<input type="number" class="input" bind:value={maxAccountsTotal} min="1" max="10000" required />
					</label>
					<label class="block space-y-1">
						<span class="label">SSE на пользователя (1..100)</span>
						<input type="number" class="input" bind:value={ssePerUser} min="1" max="100" required />
					</label>
					<label class="block space-y-1">
						<span class="label">TG кодов в час на хост (≥ 1)</span>
						<input type="number" class="input" bind:value={tgCodesPerHour} min="1" required />
					</label>
					<label class="block space-y-1">
						<span class="label">TG кодов в час на аккаунт (≥ 1)</span>
						<input type="number" class="input" bind:value={tgCodesPerAccountHour} min="1" required />
					</label>
				</div>
			</div>

			<!-- Engine bounds -->
			<div class="card space-y-3">
				<h2 class="text-sm font-semibold text-fg">Границы настроек движка (Engine bounds)</h2>
				<p class="text-xs text-fg-muted">Допустимые рамки для индивидуальных настроек аккаунтов.</p>
				<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
					<label class="block space-y-1">
						<span class="label">Мин. интервал запросов (0..60 с)</span>
						<input
							type="number"
							step="any"
							class="input"
							bind:value={minRequestIntervalSMin}
							min="0"
							max="60"
							required
						/>
					</label>
					<label class="block space-y-1">
						<span class="label">Мин. пауза антифлуда (0..600 с)</span>
						<input
							type="number"
							step="any"
							class="input"
							bind:value={antifloodPauseSMin}
							min="0"
							max="600"
							required
						/>
					</label>
					<label class="block space-y-1">
						<span class="label">Макс. повторов антифлуда (≥ 0)</span>
						<input type="number" class="input" bind:value={antifloodRetryMaxMax} min="0" required />
					</label>
					<label class="block space-y-1">
						<span class="label">Макс. TTL действия (&gt; 0..3600 с)</span>
						<input
							type="number"
							step="any"
							class="input"
							bind:value={actionTtlSMax}
							min="0.1"
							max="3600"
							required
						/>
					</label>
				</div>
			</div>

			<div class="flex items-center gap-3">
				<button type="submit" class="btn btn-primary" disabled={saveBusy}>
					{saveBusy ? 'Сохранение…' : 'Сохранить настройки'}
				</button>
				<span class="text-xs text-fg-muted">Версия: {store.serverSettings.version}</span>
			</div>
		</form>
	{/if}
</div>
