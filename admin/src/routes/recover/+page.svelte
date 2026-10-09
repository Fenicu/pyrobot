<script lang="ts">
	import { goto } from '$app/navigation';
	import { ApiFailure } from '$lib/api/errors';
	import { recoverFinish, recoverStart } from '$lib/api/public';
	import { session as defaultSession, startApp as defaultStartApp, stopApp as defaultStopApp } from '$lib/app.svelte';
	import AuthHeader from '$lib/components/auth/AuthHeader.svelte';
	import type { Session } from '$lib/stores/session.svelte';

	interface Props {
		session?: Session;
		stopApp?: () => void;
		startApp?: () => void;
		fetchImpl?: typeof fetch;
	}

	let {
		session = defaultSession,
		stopApp = defaultStopApp,
		startApp = defaultStartApp,
		fetchImpl
	}: Props = $props();

	let step = $state<1 | 2>(1);
	// false — к шагу 2 перешли по «У меня уже есть код», recover/start не вызывался.
	let sent = $state(false);
	let kind = $state<'tg' | 'recovery'>('tg');

	let login = $state('');
	let tgCode = $state('');
	let recoveryCode = $state('');
	let password = $state('');
	let repeat = $state('');

	let error = $state('');
	let busy = $state(false);

	const MIN_PASSWORD = 12;
	const problem = $derived(
		password.length > 0 && password.length < MIN_PASSWORD
			? `не короче ${MIN_PASSWORD} символов`
			: repeat.length > 0 && repeat !== password
				? 'пароли не совпадают'
				: ''
	);

	async function submitStart(e: SubmitEvent) {
		e.preventDefault();
		if (!login.trim()) return;
		busy = true;
		error = '';
		try {
			await recoverStart(login.trim(), fetchImpl);
			sent = true;
			step = 2;
		} catch (err) {
			error = err instanceof ApiFailure ? err.message : String(err);
		} finally {
			busy = false;
		}
	}

	function skipStart() {
		if (!login.trim()) return;
		error = '';
		sent = false;
		step = 2;
	}

	async function submitFinish(e: SubmitEvent) {
		e.preventDefault();
		const codeVal = kind === 'tg' ? tgCode.trim() : null;
		const recVal = kind === 'recovery' ? recoveryCode.trim() : null;
		if (problem || password.length < MIN_PASSWORD || password !== repeat || (!codeVal && !recVal)) {
			return;
		}
		busy = true;
		error = '';
		try {
			const me = await recoverFinish(
				{
					login: login.trim(),
					password,
					...(codeVal ? { code: codeVal } : {}),
					...(recVal ? { recovery_code: recVal } : {})
				},
				fetchImpl
			);
			stopApp();
			session.adopt(me);
			startApp();
			await goto('/');
		} catch (err) {
			error = err instanceof ApiFailure ? err.message : String(err);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Восстановление пароля · pyrobot</title></svelte:head>

<main class="flex min-h-dvh items-center justify-center p-4">
	<div class="card w-full max-w-sm space-y-4 p-6">
		<AuthHeader subtitle="Восстановление пароля" />

		{#if step === 1}
			<form class="space-y-4" onsubmit={submitStart}>
				<label class="block space-y-1">
					<span class="label">Логин</span>
					<input class="input" bind:value={login} autocomplete="username" required maxlength="64" />
				</label>
				{#if error}
					<p class="ext-text text-sm text-bad-fg" role="alert">{error}</p>
				{/if}
				<button type="submit" class="btn btn-primary w-full" disabled={busy || !login.trim()}>
					{busy ? 'Отправка…' : 'Получить код'}
				</button>
				<div class="flex items-center justify-between gap-2">
					<button
						type="button"
						class="text-xs text-fg-muted hover:text-fg disabled:opacity-50"
						disabled={busy || !login.trim()}
						onclick={skipStart}
					>
						У меня уже есть код
					</button>
					<a href="/login" class="text-xs text-fg-muted hover:text-fg">Вспомнили пароль? Войти</a>
				</div>
			</form>
		{:else if step === 2}
			<p class="rounded-md border border-line bg-surface-2 p-3 text-xs text-fg-muted">
				{#if sent}
					Если у учётки есть аккаунт онлайн в Telegram, код отправлен в «Избранное».
				{:else}
					Введите код из «Избранного» Telegram (действует 10 минут) или код восстановления для
					учётки <span class="font-mono">{login.trim()}</span>.
				{/if}
			</p>
			<div class="flex gap-2">
				<button
					type="button"
					class="chip"
					aria-pressed={kind === 'tg'}
					onclick={() => (kind = 'tg')}
				>
					Код из Telegram
				</button>
				<button
					type="button"
					class="chip"
					aria-pressed={kind === 'recovery'}
					onclick={() => (kind = 'recovery')}
				>
					Код восстановления
				</button>
			</div>
			<form class="space-y-3" onsubmit={submitFinish}>
				{#if kind === 'tg'}
					<label class="block space-y-1">
						<span class="label">Код из Telegram</span>
						<input
							class="input font-mono"
							bind:value={tgCode}
							required
							placeholder="8 цифр"
							maxlength="16"
						/>
					</label>
				{:else}
					<label class="block space-y-1">
						<span class="label">Код восстановления</span>
						<input
							class="input font-mono"
							bind:value={recoveryCode}
							required
							placeholder="412-K7QM2-XH9TD"
							maxlength="32"
						/>
					</label>
				{/if}
				<label class="block space-y-1">
					<span class="label">Новый пароль (не короче {MIN_PASSWORD})</span>
					<input
						class="input"
						type="password"
						bind:value={password}
						autocomplete="new-password"
						required
						minlength={MIN_PASSWORD}
						maxlength={1024}
					/>
				</label>
				<label class="block space-y-1">
					<span class="label">Новый пароль ещё раз</span>
					<input
						class="input"
						type="password"
						bind:value={repeat}
						autocomplete="new-password"
						required
						maxlength={1024}
					/>
				</label>
				{#if problem}
					<p class="text-sm text-warn-fg">{problem}</p>
				{/if}
				{#if error}
					<p class="ext-text text-sm text-bad-fg" role="alert">{error}</p>
				{/if}
				<button
					type="submit"
					class="btn btn-primary w-full"
					disabled={busy || !!problem || password.length < MIN_PASSWORD || password !== repeat || (kind === 'tg' ? !tgCode.trim() : !recoveryCode.trim())}
				>
					{busy ? 'Сохранение…' : 'Сменить пароль и войти'}
				</button>
			</form>
		{/if}
	</div>
</main>
