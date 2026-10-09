<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { ActionOut } from '$lib/api/types';
	import type { LiveEvent } from '$lib/live/sse';
	import { clock } from '$lib/util/clock.svelte';
	import { fmtMoment } from '$lib/util/format';
	import { ACTION_STATUS, actionCommand, SOURCE, statusTone } from '$lib/util/game';
	import Pill from '../Pill.svelte';
	import Row from '../ui/Row.svelte';
	import RunSteps from './RunSteps.svelte';

	interface Props {
		api: AccountApi;
		id: number;
		subscribe?: (handler: (e: LiveEvent) => void) => () => void;
	}
	let { api, id, subscribe }: Props = $props();
	let action = $state<ActionOut | null>(null);
	let error = $state('');
	const now = $derived(clock.now);

	async function load(current: number) {
		try {
			const a = await call(api.GET('/actions/{action_id}', { params: { path: { action_id: current } } }));
			if (current !== id) return;
			action = a;
			error = '';
		} catch (e) {
			if (current === id) error = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		action = null;
		error = '';
		void load(id);
	});

	$effect(() => {
		if (!subscribe) return;
		return subscribe((e) => {
			if (e.type === 'action' && e.data.id === id) void load(id);
		});
	});

	const command = $derived(action ? actionCommand(action.kind, action.payload) : '');
	const chatTitle = $derived(
		action && typeof action.payload.chat_title === 'string' ? action.payload.chat_title : null
	);
</script>

{#if error}
	<p class="ext-text text-sm text-bad-fg">{error}</p>
{:else if !action}
	<p class="text-sm text-fg-muted">Действие #{id}…</p>
{:else}
	<h3 class="mb-1 font-semibold">Действие #{action.id} · {fmtMoment(action.created_at, now, true)}</h3>
	<dl>
		<Row dl label="Статус">
			<Pill tone={statusTone(action.status)}>{ACTION_STATUS[action.status] ?? action.status}</Pill>
		</Row>
		{#if action.reason}<Row dl label="Причина"><span class="ext-text">{action.reason}</span></Row>{/if}
		<Row dl label="Команда"><span class="ext-text font-mono text-xs">{command}</span></Row>
		<Row dl label="Вид">{action.kind} · {action.command_class}</Row>
		{#if action.kind === 'forward'}
			<Row dl label="Куда"><span class="ext-text">{chatTitle ? `«${chatTitle}»` : 'название не известно'} · {action.chat_id}</span></Row>
		{/if}
		<Row dl label="Источник">{SOURCE[action.source] ?? action.source}</Row>
		{#if action.idempotency_key}<Row dl label="Ключ"><span class="font-mono text-xs break-all">{action.idempotency_key}</span></Row>{/if}
		<Row dl label="Попытки">{action.attempts}</Row>
		{#if action.answer}<Row dl label="Ответ"><span class="ext-text">{action.answer}</span></Row>{/if}
		{#if action.match_detail}<Row dl label="Совпадение"><span class="ext-text">{action.match_detail}</span></Row>{/if}
		{#if action.sent_at}<Row dl label="Отправлено">{fmtMoment(action.sent_at, now, true)}</Row>{/if}
		{#if action.finished_at}<Row dl label="Завершено">{fmtMoment(action.finished_at, now, true)}</Row>{/if}
		{#if action.reconciled_at}<Row dl label="Сверено">{fmtMoment(action.reconciled_at, now, true)}</Row>{/if}
	</dl>
	{#if action.scenario_run_id}
		<h4 class="mt-3 mb-1 text-xs text-fg-muted uppercase">Запуск</h4>
		<RunSteps {api} runId={action.scenario_run_id} {subscribe} />
	{/if}
{/if}
