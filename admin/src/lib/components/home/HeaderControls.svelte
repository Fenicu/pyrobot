<script lang="ts">
	import OctagonX from '@lucide/svelte/icons/octagon-x';
	import Pause from '@lucide/svelte/icons/pause';
	import Play from '@lucide/svelte/icons/play';
	import Power from '@lucide/svelte/icons/power';
	import type { AccountApi } from '$lib/api/account';
	import { ApiFailure } from '$lib/api/errors';
	import type { EngineStatus } from '$lib/api/types';
	import { kill, setMode, setPaused, unkill } from '$lib/controls';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';

	interface Props {
		api: AccountApi;
		status: EngineStatus | null;
		/** После действия: перечитать статус. */
		onchange: () => void;
	}
	let { api, status, onchange }: Props = $props();
	let busy = $state(false);

	async function run(action: () => Promise<unknown>, done: string) {
		busy = true;
		try {
			await action();
			toasts.show(done, 'ok');
			onchange();
		} catch (e) {
			toasts.show(e instanceof ApiFailure ? e.message : String(e), 'error');
		} finally {
			busy = false;
		}
	}

	async function togglePause() {
		if (!status) return;
		const pause = !status.paused;
		await run(() => setPaused(api, pause), pause ? 'Планировщик на паузе' : 'Планировщик продолжает');
	}

	async function toggleKill() {
		if (!status) return;
		if (status.killed) {
			const ok = await dialogs.confirm({
				title: 'Снять kill?',
				body: 'Шлюз снова начнёт отправлять команды в игру.',
				confirmText: 'Снять kill'
			});
			if (ok) await run(() => unkill(api), 'Kill снят');
			return;
		}
		const reason = await dialogs.prompt({
			title: 'Kill: остановить все отправки',
			body: 'Шлюз перестанет отправлять что-либо в игру, пока kill не снят.',
			input: { label: 'Причина', placeholder: 'например: проверка', maxlength: 200, required: true },
			confirmText: 'Остановить',
			danger: true
		});
		if (reason) await run(() => kill(api, reason), 'Kill включён');
	}

	async function toggleMode() {
		if (!status) return;
		const target = status.mode === 'live' ? 'dry_run' : 'live';
		if (target === 'live') {
			const ok = await dialogs.confirm({
				title: 'Включить LIVE?',
				body: 'Бот начнёт реально тратить ресурсы: команды действий уйдут в игру.',
				confirmText: 'Включить live',
				danger: true
			});
			if (!ok) return;
		}
		await run(
			() => setMode(api, target),
			target === 'live' ? 'Режим LIVE' : 'Режим DRY RUN: действия не уходят в игру'
		);
	}
</script>

<!-- Режим без движка пишется прямо в настройки, пауза и kill — только через движок. -->
{#if status}
	{@const stopped = 'Пауза и kill — у запущенного движка'}
	<div class="flex flex-wrap items-center gap-2" role="group" aria-label="Управление">
		<button
			type="button"
			class="btn"
			title={!status.running
				? stopped
				: status.paused
					? 'Снять паузу: бот снова сам принимает решения'
					: 'Бот перестаёт решать сам; «Проснуться» при ограблении и ручные команды по умолчанию проходят'}
			disabled={busy || !status.running}
			onclick={togglePause}
		>
			{#if status.paused}
				<Play class="size-4" aria-hidden="true" /> Продолжить
			{:else}
				<Pause class="size-4" aria-hidden="true" /> Пауза
			{/if}
		</button>
		<button
			type="button"
			class="btn"
			title={status.mode === 'live'
				? 'Бот решает, но действия, кроме навигации, в игру не отправляет — только пишет в журнал'
				: 'Бот начнёт реально отправлять действия в игру'}
			disabled={busy}
			onclick={toggleMode}
		>
			<Power class="size-4" aria-hidden="true" />
			{status.mode === 'live' ? 'В dry_run' : 'Включить live'}
		</button>
		<button
			type="button"
			class="btn {status.killed ? '' : 'btn-danger'}"
			title={!status.running
				? stopped
				: status.killed
					? 'Снять аварийный стоп: отправки в игру снова разрешены'
					: 'Аварийный стоп: в игру не уходит ничего, даже ручные команды и «Проснуться»'}
			disabled={busy || !status.running}
			onclick={toggleKill}
		>
			<OctagonX class="size-4" aria-hidden="true" />
			{status.killed ? 'Снять kill' : 'Kill'}
		</button>
	</div>
{/if}
