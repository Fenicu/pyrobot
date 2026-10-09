<script lang="ts">
	import { onMount } from 'svelte';
	import type { AdminStore } from '$lib/admin/store.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';

	interface Props {
		store: AdminStore;
	}

	let { store }: Props = $props();

	// Retention
	let messagesDays = $state<number | null>(90);
	let decisionsDays = $state<number | null>(30);
	let metricsDays = $state<number | null>(365);
	let ledgerDays = $state<number | null>(31);
	let auditDays = $state<number | null>(365);

	// Invites
	let defaultTtlH = $state<number | null>(72);
	let defaultMaxAccounts = $state<number | null>(1);

	// Limits
	let maxAccountsTotal = $state<number | null>(50);
	let ssePerUser = $state<number | null>(5);
	let tgCodesPerHour = $state<number | null>(10);
	let tgCodesPerAccountHour = $state<number | null>(3);

	// Engine bounds
	let minRequestIntervalSMin = $state<number | null>(1.6);
	let antifloodPauseSMin = $state<number | null>(10.0);
	let antifloodRetryMaxMax = $state<number | null>(2);
	let actionTtlSMax = $state<number | null>(600.0);

	let saveBusy = $state(false);
	let validationError = $state('');

	interface SchemaFieldDef {
		type?: string;
		minimum?: number;
		maximum?: number;
		exclusiveMinimum?: number | boolean;
		exclusiveMaximum?: number | boolean;
	}

	function findSchemaProperty(schema: any, section: string, field: string): SchemaFieldDef | undefined {
		if (!schema || typeof schema !== 'object') return undefined;

		if (schema.properties?.[field]) {
			return schema.properties[field];
		}
		if (schema.properties?.[section]?.properties?.[field]) {
			return schema.properties[section].properties[field];
		}
		const defs = schema.$defs ?? schema.definitions;
		if (defs && typeof defs === 'object') {
			for (const def of Object.values(defs)) {
				if (def && typeof def === 'object' && (def as any).properties?.[field]) {
					return (def as any).properties[field];
				}
			}
		}
		return undefined;
	}

	interface FieldSpec {
		section: string;
		field: string;
		label: string;
		type: 'integer' | 'number';
		min?: number;
		max?: number;
		exclusiveMin?: boolean;
		unit?: string;
	}

	const DEFAULT_SPECS: Record<
		string,
		{ label: string; type: 'integer' | 'number'; min?: number; max?: number; exclusiveMin?: boolean; unit?: string }
	> = {
		messages_days: { label: 'Хранение сообщений', type: 'integer', min: 1, max: 3650, unit: 'дней' },
		decisions_days: { label: 'Хранение решений', type: 'integer', min: 1, max: 3650, unit: 'дней' },
		metrics_days: { label: 'Хранение метрик', type: 'integer', min: 1, max: 3650, unit: 'дней' },
		ledger_days: { label: 'Хранение прихода (ledger)', type: 'integer', min: 31, max: 3650, unit: 'дней' },
		audit_days: { label: 'Хранение журнала действий', type: 'integer', min: 1, max: 3650, unit: 'дней' },

		default_ttl_h: { label: 'Срок приглашений по умолчанию', type: 'integer', min: 1, max: 720, unit: 'часов' },
		default_max_accounts: { label: 'Лимит аккаунтов по умолчанию', type: 'integer', min: 1, max: 1000 },

		max_accounts_total: { label: 'Максимум аккаунтов на сервере', type: 'integer', min: 1, max: 10000 },
		sse_per_user: { label: 'SSE-подключений на пользователя', type: 'integer', min: 1, max: 100 },
		tg_codes_per_hour: { label: 'Кодов Telegram в час на хост', type: 'integer', min: 1 },
		tg_codes_per_account_hour: { label: 'Кодов Telegram в час на аккаунт', type: 'integer', min: 1 },

		min_request_interval_s_min: { label: 'Мин. интервал между запросами', type: 'number', min: 0, max: 60, unit: 'с' },
		antiflood_pause_s_min: { label: 'Мин. пауза антифлуда', type: 'number', min: 0, max: 600, unit: 'с' },
		antiflood_retry_max_max: { label: 'Макс. число повторов антифлуда', type: 'integer', min: 0 },
		action_ttl_s_max: { label: 'Макс. срок действия', type: 'number', min: 0, exclusiveMin: true, max: 3600, unit: 'с' }
	};

	function getFieldSpec(section: string, field: string): FieldSpec {
		const fallback = DEFAULT_SPECS[field] ?? {
			label: field,
			type: 'number' as const,
			min: undefined,
			max: undefined,
			exclusiveMin: false,
			unit: undefined
		};
		const schema = store.serverSettings?.schema;
		const prop = findSchemaProperty(schema, section, field);

		let type = fallback.type;
		if (prop?.type === 'integer' || prop?.type === 'number') {
			type = prop.type;
		}

		let min = fallback.min;
		let exclusiveMin = fallback.exclusiveMin ?? false;
		if (prop?.minimum !== undefined) {
			min = prop.minimum;
			exclusiveMin = false;
		}
		if (prop?.exclusiveMinimum !== undefined) {
			if (typeof prop.exclusiveMinimum === 'number') {
				min = prop.exclusiveMinimum;
				exclusiveMin = true;
			} else if (prop.exclusiveMinimum === true && prop.minimum !== undefined) {
				min = prop.minimum;
				exclusiveMin = true;
			}
		}

		let max = fallback.max;
		if (prop?.maximum !== undefined) {
			max = prop.maximum;
		}
		if (prop?.exclusiveMaximum !== undefined && typeof prop.exclusiveMaximum === 'number') {
			max = prop.exclusiveMaximum;
		}

		return {
			section,
			field,
			label: fallback.label,
			type,
			min,
			max,
			exclusiveMin,
			unit: fallback.unit
		};
	}

	function validateValue(val: unknown, spec: FieldSpec): string | null {
		if (val === null || val === undefined || val === '' || Number.isNaN(val)) {
			return `Поле «${spec.label}» не должно быть пустым`;
		}

		const num = Number(val);
		if (Number.isNaN(num)) {
			return `Поле «${spec.label}» должно быть числом`;
		}

		if (spec.type === 'integer' && !Number.isInteger(num)) {
			return `Поле «${spec.label}» должно быть целым числом`;
		}

		const unit = spec.unit ? ` ${spec.unit}` : '';

		if (spec.min !== undefined) {
			const violated = spec.exclusiveMin ? num <= spec.min : num < spec.min;
			if (violated) {
				if (spec.max !== undefined) {
					const minVal = spec.exclusiveMin ? (spec.min === 0 ? 0.1 : spec.min) : spec.min;
					return `${spec.label} должно быть от ${minVal} до ${spec.max}${unit}`;
				}
				const op = spec.exclusiveMin ? '> ' : 'не меньше ';
				return `${spec.label} должно быть ${op}${spec.min}${unit}`;
			}
		}

		if (spec.max !== undefined && num > spec.max) {
			if (spec.min !== undefined) {
				const minVal = spec.exclusiveMin ? (spec.min === 0 ? 0.1 : spec.min) : spec.min;
				return `${spec.label} должно быть от ${minVal} до ${spec.max}${unit}`;
			}
			return `${spec.label} должно быть не больше ${spec.max}${unit}`;
		}

		return null;
	}

	function syncFromStore() {
		const v = store.serverSettings?.values as Record<string, Record<string, unknown>> | undefined;
		if (!v) return;

		const ret = v.retention ?? {};
		messagesDays = ret.messages_days !== undefined ? Number(ret.messages_days) : 90;
		decisionsDays = ret.decisions_days !== undefined ? Number(ret.decisions_days) : 30;
		metricsDays = ret.metrics_days !== undefined ? Number(ret.metrics_days) : 365;
		ledgerDays = ret.ledger_days !== undefined ? Number(ret.ledger_days) : 31;
		auditDays = ret.audit_days !== undefined ? Number(ret.audit_days) : 365;

		const inv = v.invites ?? {};
		defaultTtlH = inv.default_ttl_h !== undefined ? Number(inv.default_ttl_h) : 72;
		defaultMaxAccounts = inv.default_max_accounts !== undefined ? Number(inv.default_max_accounts) : 1;

		const lim = v.limits ?? {};
		maxAccountsTotal = lim.max_accounts_total !== undefined ? Number(lim.max_accounts_total) : 50;
		ssePerUser = lim.sse_per_user !== undefined ? Number(lim.sse_per_user) : 5;
		tgCodesPerHour = lim.tg_codes_per_hour !== undefined ? Number(lim.tg_codes_per_hour) : 10;
		tgCodesPerAccountHour = lim.tg_codes_per_account_hour !== undefined ? Number(lim.tg_codes_per_account_hour) : 3;

		const eb = v.engine_bounds ?? {};
		minRequestIntervalSMin = eb.min_request_interval_s_min !== undefined ? Number(eb.min_request_interval_s_min) : 1.6;
		antifloodPauseSMin = eb.antiflood_pause_s_min !== undefined ? Number(eb.antiflood_pause_s_min) : 10.0;
		antifloodRetryMaxMax = eb.antiflood_retry_max_max !== undefined ? Number(eb.antiflood_retry_max_max) : 2;
		actionTtlSMax = eb.action_ttl_s_max !== undefined ? Number(eb.action_ttl_s_max) : 600.0;
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

		const fieldsToValidate: [unknown, string, string][] = [
			[messagesDays, 'retention', 'messages_days'],
			[decisionsDays, 'retention', 'decisions_days'],
			[metricsDays, 'retention', 'metrics_days'],
			[ledgerDays, 'retention', 'ledger_days'],
			[auditDays, 'retention', 'audit_days'],
			[defaultTtlH, 'invites', 'default_ttl_h'],
			[defaultMaxAccounts, 'invites', 'default_max_accounts'],
			[maxAccountsTotal, 'limits', 'max_accounts_total'],
			[ssePerUser, 'limits', 'sse_per_user'],
			[tgCodesPerHour, 'limits', 'tg_codes_per_hour'],
			[tgCodesPerAccountHour, 'limits', 'tg_codes_per_account_hour'],
			[minRequestIntervalSMin, 'engine_bounds', 'min_request_interval_s_min'],
			[antifloodPauseSMin, 'engine_bounds', 'antiflood_pause_s_min'],
			[antifloodRetryMaxMax, 'engine_bounds', 'antiflood_retry_max_max'],
			[actionTtlSMax, 'engine_bounds', 'action_ttl_s_max']
		];

		for (const [val, section, field] of fieldsToValidate) {
			const spec = getFieldSpec(section, field);
			const err = validateValue(val, spec);
			if (err) {
				validationError = err;
				return;
			}
		}

		const changes = {
			retention: {
				messages_days: Number(messagesDays),
				decisions_days: Number(decisionsDays),
				metrics_days: Number(metricsDays),
				ledger_days: Number(ledgerDays),
				audit_days: Number(auditDays)
			},
			invites: {
				default_ttl_h: Number(defaultTtlH),
				default_max_accounts: Number(defaultMaxAccounts)
			},
			limits: {
				max_accounts_total: Number(maxAccountsTotal),
				sse_per_user: Number(ssePerUser),
				tg_codes_per_hour: Number(tgCodesPerHour),
				tg_codes_per_account_hour: Number(tgCodesPerAccountHour)
			},
			engine_bounds: {
				min_request_interval_s_min: Number(minRequestIntervalSMin),
				antiflood_pause_s_min: Number(antifloodPauseSMin),
				antiflood_retry_max_max: Number(antifloodRetryMaxMax),
				action_ttl_s_max: Number(actionTtlSMax)
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

<div class="space-y-[14px]">
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
		<form onsubmit={save} novalidate class="space-y-[14px]">
			<div class="grid items-start gap-[14px] xl:grid-cols-2">
				<!-- Retention -->
				<div class="card space-y-3">
					<h2 class="card-title mb-0">Сроки хранения данных (Retention)</h2>
					<p class="text-xs text-fg-muted">Политика очистки старых данных сервера и аккаунтов (в днях).</p>
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-2">
						<label class="block space-y-1">
							<span class="label block">Сообщения (1..3650 дн)</span>
							<input type="number" class="input" bind:value={messagesDays} min="1" max="3650" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">Решения (1..3650 дн)</span>
							<input type="number" class="input" bind:value={decisionsDays} min="1" max="3650" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">Метрики (1..3650 дн)</span>
							<input type="number" class="input" bind:value={metricsDays} min="1" max="3650" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">Приход / леджер (31..3650 дн)</span>
							<input type="number" class="input" bind:value={ledgerDays} min="31" max="3650" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">Журнал действий / аудит (1..3650 дн)</span>
							<input type="number" class="input" bind:value={auditDays} min="1" max="3650" required />
						</label>
					</div>
				</div>

				<!-- Invites defaults -->
				<div class="card space-y-3">
					<h2 class="card-title mb-0">Приглашения по умолчанию</h2>
					<p class="text-xs text-fg-muted">Значения по умолчанию для создаваемых приглашений.</p>
					<div class="grid gap-3 sm:grid-cols-2">
						<label class="block space-y-1">
							<span class="label block">Срок действия по умолчанию (1..720 ч)</span>
							<input type="number" class="input" bind:value={defaultTtlH} min="1" max="720" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">Лимит аккаунтов по умолчанию (1..1000)</span>
							<input type="number" class="input" bind:value={defaultMaxAccounts} min="1" max="1000" required />
						</label>
					</div>
				</div>

				<!-- Limits -->
				<div class="card space-y-3">
					<h2 class="card-title mb-0">Ограничения сервера (Limits)</h2>
					<p class="text-xs text-fg-muted">Лимиты емкости и запросов Telegram.</p>
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-2">
						<label class="block space-y-1">
							<span class="label block">Всего аккаунтов (1..10000)</span>
							<input type="number" class="input" bind:value={maxAccountsTotal} min="1" max="10000" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">SSE на пользователя (1..100)</span>
							<input type="number" class="input" bind:value={ssePerUser} min="1" max="100" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">TG кодов в час на хост (≥ 1)</span>
							<input type="number" class="input" bind:value={tgCodesPerHour} min="1" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">TG кодов в час на аккаунт (≥ 1)</span>
							<input type="number" class="input" bind:value={tgCodesPerAccountHour} min="1" required />
						</label>
					</div>
				</div>

				<!-- Engine bounds -->
				<div class="card space-y-3">
					<h2 class="card-title mb-0">Границы настроек движка (Engine bounds)</h2>
					<p class="text-xs text-fg-muted">Допустимые рамки для индивидуальных настроек аккаунтов.</p>
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-2">
						<label class="block space-y-1">
							<span class="label block">Мин. интервал запросов (0..60 с)</span>
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
							<span class="label block">Мин. пауза антифлуда (0..600 с)</span>
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
							<span class="label block">Макс. повторов антифлуда (≥ 0)</span>
							<input type="number" class="input" bind:value={antifloodRetryMaxMax} min="0" required />
						</label>
						<label class="block space-y-1">
							<span class="label block">Макс. TTL действия (&gt; 0..3600 с)</span>
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
