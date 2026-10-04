<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { ApiFailure } from '$lib/api/errors';
	import { acceptInvite, peekInvite } from '$lib/api/public';
	import { session as defaultSession, startApp as defaultStartApp, stopApp as defaultStopApp } from '$lib/app.svelte';
	import RecoveryCodes from '$lib/components/auth/RecoveryCodes.svelte';
	import type { Session } from '$lib/stores/session.svelte';

	interface Props {
		token?: string;
		session?: Session;
		stopApp?: () => void;
		startApp?: () => void;
		fetchImpl?: typeof fetch;
	}

	let {
		token: propToken,
		session = defaultSession,
		stopApp = defaultStopApp,
		startApp = defaultStartApp,
		fetchImpl
	}: Props = $props();
	const token = $derived(propToken ?? page.params.token);

	let status = $state<'loading' | 'form' | 'codes' | 'error'>('loading');
	let errorMessage = $state('');

	let login = $state('');
	let password = $state('');
	let repeat = $state('');
	let formError = $state('');
	let busy = $state(false);

	let codes = $state<string[]>([]);
	let saved = $state(false);

	const LOGIN_RE = /^[A-Za-z0-9_.-]{3,64}$/;
	const LOGIN_HINT = 'латиница, цифры, точка, дефис, подчёркивание; 3–64 символа';
	const MIN_PASSWORD = 12;
	const problem = $derived(
		login.length > 0 && !LOGIN_RE.test(login)
			? LOGIN_HINT
			: password.length > 0 && password.length < MIN_PASSWORD
				? `не короче ${MIN_PASSWORD} символов`
				: repeat.length > 0 && repeat !== password
					? 'пароли не совпадают'
					: ''
	);

	onMount(() => {
		void check();
	});

	async function check() {
		if (!token) {
			status = 'error';
			errorMessage = 'Приглашение не найдено';
			return;
		}
		status = 'loading';
		errorMessage = '';
		try {
			await peekInvite(token, fetchImpl);
			status = 'form';
		} catch (err) {
			status = 'error';
			errorMessage = err instanceof ApiFailure ? err.message : String(err);
		}
	}

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		if (!token || problem || !LOGIN_RE.test(login) || password.length < MIN_PASSWORD || password !== repeat) {
			return;
		}
		busy = true;
		formError = '';
		try {
			const res = await acceptInvite(token, login.trim(), password, fetchImpl);
			stopApp();
			session.adopt({ login: res.login, csrf_token: res.csrf_token, role: res.role });
			startApp();
			codes = res.recovery_codes;
			status = 'codes';
		} catch (err) {
			formError = err instanceof ApiFailure ? err.message : String(err);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>Приглашение · pyrobot</title></svelte:head>

<main class="flex min-h-dvh items-center justify-center p-4">
	{#if status === 'loading'}
		<p class="p-6 text-sm text-fg-muted" role="status">Загрузка…</p>
	{:else if status === 'error'}
		<div class="card w-full max-w-sm space-y-3 p-5" role="alert">
			<h1 class="text-lg font-semibold">Приглашение</h1>
			<p class="text-sm text-bad-fg">{errorMessage}</p>
			<a href="/login" class="btn w-full">На страницу входа</a>
		</div>
	{:else if status === 'form'}
		<form class="card w-full max-w-sm space-y-4 p-5" onsubmit={submit}>
			<div>
				<h1 class="text-lg font-semibold">pyrobot</h1>
				<p class="text-sm text-fg-muted">Регистрация по приглашению</p>
			</div>
			<label class="block space-y-1">
				<span class="label">Логин</span>
				<input
					class="input"
					bind:value={login}
					autocomplete="username"
					required
					minlength={3}
					maxlength={64}
				/>
			</label>
			<label class="block space-y-1">
				<span class="label">Пароль (не короче {MIN_PASSWORD})</span>
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
				<span class="label">Пароль ещё раз</span>
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
			{#if formError}
				<p class="ext-text text-sm text-bad-fg" role="alert">{formError}</p>
			{/if}
			<button
				type="submit"
				class="btn btn-primary w-full"
				disabled={busy || !!problem || !LOGIN_RE.test(login) || password.length < MIN_PASSWORD || password !== repeat}
			>
				{busy ? 'Регистрация…' : 'Зарегистрироваться'}
			</button>
		</form>
	{:else if status === 'codes'}
		<div class="card w-full max-w-md space-y-4 p-5">
			<div>
				<h1 class="text-lg font-semibold">Коды восстановления</h1>
				<p class="text-sm text-fg-muted">
					Сохраните эти одноразовые коды. Они понадобятся для входа, если вы забудете пароль. Коды показываются один раз.
				</p>
			</div>
			<RecoveryCodes {codes} />
			<label class="flex items-center gap-2 text-sm text-fg cursor-pointer">
				<input type="checkbox" bind:checked={saved} />
				<span>Коды сохранены</span>
			</label>
			<button
				type="button"
				class="btn btn-primary w-full"
				disabled={!saved}
				onclick={() => void goto('/accounts')}
			>
				Продолжить
			</button>
		</div>
	{/if}
</main>
