<script lang="ts">
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';
	import type { components } from '$lib/api/schema';
	import type { CommandOut } from '$lib/api/types';
	import { commandResult, newKey, withConfirm, type Confirmer } from '$lib/commands';

	type SendIn = components['schemas']['SendIn'];

	interface Props {
		api: Api;
		confirmer?: Confirmer;
	}
	let { api, confirmer }: Props = $props();
	let text = $state('');
	let busy = $state(false);
	let result = $state<{ text: string; kind: string } | null>(null);
	let pending = $state(false);
	// Ключ одной отправки: повтор после 202 pending или сбоя — тот же ключ, новый текст — новый.
	let key: string | null = null;
	let keyText = '';

	async function send(e?: SubmitEvent) {
		e?.preventDefault();
		const command = text.trim();
		if (!command || busy) return;
		if (key === null || keyText !== command) {
			key = newKey('send');
			keyText = command;
		}
		busy = true;
		result = null;
		pending = false;
		try {
			const out: CommandOut | null = await withConfirm<SendIn>(
				(body) => call(api.POST('/api/v1/commands/send', { body })),
				{ text: command, idempotency_key: key },
				command,
				confirmer
			);
			if (out === null) {
				result = { text: 'Отменено', kind: 'info' };
			} else {
				result = commandResult(out);
				pending = out.status === 'pending';
				if (!pending) key = null;
			}
		} catch (err) {
			result = { text: err instanceof ApiFailure ? err.message : String(err), kind: 'error' };
			if (err instanceof ApiFailure && ['forbidden', 'invalid', 'validation'].includes(err.error.kind)) key = null;
		} finally {
			busy = false;
		}
	}

	const tone: Record<string, string> = {
		ok: 'text-ok-fg',
		warn: 'text-warn-fg',
		error: 'text-bad-fg',
		info: 'text-fg-muted'
	};
</script>

<section class="card" aria-labelledby="manual-title">
	<h2 id="manual-title" class="card-title">Ручная команда в игру</h2>
	<form class="flex gap-2" onsubmit={send}>
		<label class="flex-1">
			<span class="sr-only">Команда</span>
			<input
				class="input"
				placeholder="например /inv или 🎒Рюкзак"
				maxlength="256"
				autocomplete="off"
				bind:value={text}
			/>
		</label>
		<button type="submit" class="btn btn-primary" disabled={busy || !text.trim()}>
			{busy ? '…' : 'Отправить'}
		</button>
	</form>
	<p class="mt-1 text-xs text-fg-faint">
		forbidden и donate не уйдут никогда · risky — только после подтверждения
	</p>
	{#if result}
		<p class="ext-text mt-2 text-sm {tone[result.kind] ?? ''}" role="status">{result.text}</p>
		{#if pending}
			<button type="button" class="btn mt-1" onclick={() => send()}>Проверить итог</button>
		{/if}
	{/if}
</section>
