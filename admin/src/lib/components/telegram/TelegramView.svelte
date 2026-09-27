<script lang="ts">
	import { call, type Api } from '$lib/api/client';
	import { ApiFailure, errorText } from '$lib/api/errors';
	import type { TgStatus } from '$lib/api/types';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import Pill from '../Pill.svelte';

	interface Props {
		api: Api;
	}
	let { api }: Props = $props();
	let status = $state<TgStatus | null>(null);
	// Попытка входа живёт только в памяти этой страницы до её конца.
	let attempt = $state<string | null>(null);
	let phone = $state('');
	let code = $state('');
	let password = $state('');
	let busy = $state(false);
	let message = $state('');

	const ERRORS: Record<string, string> = {
		invalid_code: 'Неверный код — попробуйте ещё раз',
		code_expired: 'Код истёк — начните вход заново',
		invalid_password: 'Неверный пароль 2FA — попробуйте ещё раз',
		signup_required: 'Номер не зарегистрирован в Telegram',
		invalid_phone: 'Неверный номер телефона',
		password_required: 'Нужен пароль 2FA',
		unexpected_user: 'Вошли не в тот аккаунт — сервис вышел из него',
		session_revoked: 'Сессия Telegram отозвана — войдите снова',
		flood_wait: 'Telegram просит подождать',
		send_code_failed: 'Код не отправлен — Telegram недоступен',
		connect_failed: 'Нет соединения с Telegram',
		online_failed: 'Не удалось выйти в сеть после входа',
		logout_failed: 'Выход из Telegram не удался'
	};
	const STATE: Record<string, string> = {
		unauthorized: 'не выполнен вход',
		awaiting_code: 'ждёт код',
		awaiting_password: 'ждёт пароль 2FA',
		online: 'online',
		error: 'ошибка'
	};

	async function refresh() {
		try {
			status = await call(api.GET('/api/v1/tg/status'));
		} catch (e) {
			message = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	$effect(() => {
		void refresh();
	});

	/** Ход входа по автомату `state`, а не по HTTP-статусу; 400/409 — попытка устарела. */
	async function step(send: () => Promise<TgStatus>) {
		busy = true;
		message = '';
		try {
			status = await send();
			if (status.state === 'online' || status.state === 'unauthorized' || status.state === 'error') attempt = null;
			else if (status.attempt_id) attempt = status.attempt_id;
		} catch (e) {
			if (!(e instanceof ApiFailure)) throw e;
			const err = e.error;
			if (err.kind === 'conflict' || (err.kind === 'http' && err.status === 400) || err.kind === 'forbidden') {
				attempt = null;
				message =
					err.status === 400 && ERRORS[err.code] ? ERRORS[err.code]! : 'Попытка входа устарела или начата в другой вкладке — начните заново';
			} else if (err.kind === 'rate_limited') {
				message = err.retryAfter !== null ? `Telegram просит подождать ${err.retryAfter} с` : 'Telegram просит подождать';
			} else if (err.kind === 'unavailable') {
				message = `Telegram недоступен${err.code ? ` (${ERRORS[err.code] ?? err.code})` : ''}`;
			} else {
				message = errorText(err);
			}
			await refresh();
		} finally {
			busy = false;
			code = '';
			password = '';
		}
	}

	const start = (e: SubmitEvent) => {
		e.preventDefault();
		void step(() => call(api.POST('/api/v1/tg/login/start', { body: { phone: phone.trim() } })));
	};
	const sendCode = (e: SubmitEvent) => {
		e.preventDefault();
		const value = code.trim();
		void step(() => call(api.POST('/api/v1/tg/login/code', { body: { attempt_id: attempt ?? '', code: value } })));
	};
	const sendPassword = (e: SubmitEvent) => {
		e.preventDefault();
		const value = password;
		void step(() =>
			call(api.POST('/api/v1/tg/login/password', { body: { attempt_id: attempt ?? '', password: value } }))
		);
	};

	async function logout() {
		const ok = await dialogs.confirm({
			title: 'Выйти из Telegram?',
			body: 'Бот перестанет играть, пока снова не войти (код из Telegram).',
			confirmText: 'Выйти',
			danger: true
		});
		if (ok) await step(() => call(api.POST('/api/v1/tg/logout')));
	}

	const phase = $derived(status?.state ?? null);
	const waiting = $derived(phase === 'awaiting_code' || phase === 'awaiting_password');
</script>

<div class="max-w-lg space-y-3">
	<section class="card" aria-labelledby="tg-status">
		<h2 id="tg-status" class="card-title">Статус</h2>
		{#if status}
			<p class="flex flex-wrap items-center gap-2 text-sm">
				<Pill tone={status.state === 'online' ? 'ok' : status.state === 'error' ? 'bad' : 'warn'}>
					{STATE[status.state] ?? status.state}
				</Pill>
				{#if status.user_id}<span>user_id <span class="font-mono">{status.user_id}</span></span>{/if}
			</p>
			{#if status.error}
				<p class="ext-text mt-1 text-sm text-bad-fg">{ERRORS[status.error] ?? status.error}</p>
			{/if}
		{:else}
			<p class="text-sm text-fg-muted">…</p>
		{/if}
		{#if message}<p class="ext-text mt-2 text-sm text-warn-fg" role="alert">{message}</p>{/if}
	</section>

	{#if phase === 'online'}
		<button type="button" class="btn btn-danger" disabled={busy} onclick={logout}>Выйти из Telegram</button>
	{:else if waiting && attempt === null}
		<section class="card space-y-2 text-sm">
			<p>Вход уже начат в другой вкладке или сессии. Можно начать заново здесь.</p>
			<form class="flex gap-2" onsubmit={start}>
				<label class="flex-1"><span class="sr-only">Телефон</span>
					<input class="input" type="tel" placeholder="+7…" autocomplete="off" bind:value={phone} required />
				</label>
				<button type="submit" class="btn btn-primary" disabled={busy || !phone.trim()}>Получить код</button>
			</form>
		</section>
	{:else if phase === 'awaiting_code'}
		<form class="card space-y-2" onsubmit={sendCode}>
			<label class="block space-y-1">
				<span class="label">Код из Telegram</span>
				<input class="input" inputmode="numeric" autocomplete="off" name="tg-code" bind:value={code} required />
			</label>
			<button type="submit" class="btn btn-primary" disabled={busy || !code.trim()}>Отправить код</button>
		</form>
	{:else if phase === 'awaiting_password'}
		<form class="card space-y-2" onsubmit={sendPassword}>
			<label class="block space-y-1">
				<span class="label">Пароль 2FA</span>
				<input class="input" type="password" autocomplete="off" name="tg-2fa" bind:value={password} required />
			</label>
			<button type="submit" class="btn btn-primary" disabled={busy || !password}>Войти</button>
		</form>
	{:else if status}
		<form class="card space-y-2" onsubmit={start}>
			<label class="block space-y-1">
				<span class="label">Телефон аккаунта</span>
				<input class="input" type="tel" placeholder="+7…" autocomplete="off" bind:value={phone} required />
			</label>
			<button type="submit" class="btn btn-primary" disabled={busy || !phone.trim()}>Получить код</button>
		</form>
	{/if}
</div>
