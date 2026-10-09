<script lang="ts">
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure } from '$lib/api/errors';

	interface Props {
		api: Api;
		/** Пароль сменён: сервер закрыл все сессии. */
		ondone: () => void;
	}
	let { api, ondone }: Props = $props();
	const titleId = $props.id();
	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let error = $state('');
	let busy = $state(false);
	const MIN = 12;
	const problem = $derived(
		next.length > 0 && next.length < MIN
			? `не короче ${MIN} символов`
			: repeat.length > 0 && repeat !== next
				? 'пароли не совпадают'
				: ''
	);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (problem || !current || !next) return;
		busy = true;
		error = '';
		try {
			await call(api.POST('/api/v1/auth/password', { body: { current, new: next } }));
			current = next = repeat = '';
			ondone();
		} catch (err) {
			error = err instanceof ApiFailure ? err.message : String(err);
		} finally {
			busy = false;
		}
	}
</script>

<form class="card space-y-3" onsubmit={submit} aria-labelledby={titleId}>
	<h2 id={titleId} class="card-title mb-0">Пароль</h2>
	<label class="block space-y-1">
		<span class="label">Текущий пароль</span>
		<input class="input" type="password" autocomplete="current-password" bind:value={current} required maxlength="1024" />
	</label>
	<label class="block space-y-1">
		<span class="label">Новый пароль (не короче {MIN})</span>
		<input class="input" type="password" autocomplete="new-password" bind:value={next} required minlength={MIN} maxlength="1024" />
	</label>
	<label class="block space-y-1">
		<span class="label">Новый пароль ещё раз</span>
		<input class="input" type="password" autocomplete="new-password" bind:value={repeat} required maxlength="1024" />
	</label>
	{#if problem}<p class="text-sm text-warn-fg">{problem}</p>{/if}
	{#if error}<p class="ext-text text-sm text-bad-fg" role="alert">{error}</p>{/if}
	<p class="text-xs text-fg-faint">После смены все сессии закроются — войдите заново.</p>
	<button type="submit" class="btn btn-primary" disabled={busy || !!problem || !current || !next || repeat !== next}>
		Сменить пароль
	</button>
</form>
