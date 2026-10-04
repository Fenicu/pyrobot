<script lang="ts">
	import type { AccountApi } from '$lib/api/account';
	import { call } from '$lib/api/client';
	import { ApiFailure, errorText } from '$lib/api/errors';
	import type { EngineStatus, TgStatus } from '$lib/api/types';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { tgStateLabel } from '$lib/util/game';
	import Pill from '../Pill.svelte';

	interface Props {
		api: AccountApi;
		/** Статус движка аккаунта (`EngineStore`): по нему перечитывается статус входа. */
		engine: { readonly status: EngineStatus | null };
	}
	let { api, engine }: Props = $props();
	let status = $state<TgStatus | null>(null);
	// Попытка входа живёт только в памяти этой страницы до её конца.
	let attempt = $state<string | null>(null);
	let phone = $state('');
	let code = $state('');
	let password = $state('');
	let busy = $state(false);
	let message = $state('');
	// Своё приложение Telegram: api_hash — секрет, после отправки не хранится.
	let appId = $state('');
	let appHash = $state('');
	let appBusy = $state(false);
	let appError = $state('');

	const TG_APP_ID_MAX = 2 ** 31 - 1;
	// Приложение не сменить, пока аккаунт в Telegram: сервер откажет 409 `tg_logged_in`.
	const app = $derived(status === null ? null : (status.app ?? 'server'));
	const inTelegram = $derived(status?.state === 'online' || status?.state === 'overload');
	// При остановленном движке статус «stopped» не отличает вошедшего от вышедшего (движка нет,
	// сессия — в базе). Если сервер отказал 409 `tg_logged_in`, подсказку держит флаг, пока
	// перечитанный статус не покажет, что входа нет.
	let logged = $state(false);
	$effect(() => {
		if (status !== null && status.state !== 'stopped') logged = false;
	});
	const appLocked = $derived(inTelegram || logged);

	/** Проверка как у сервера (`PUT /tg/app`): пустое отсечено неактивной кнопкой сохранения. */
	function checkApp(): string | null {
		// Number() понимает `1e3`, `0x1F` и `+5` как числа — пропускаем только цифры без знака.
		const raw = appId.trim();
		const id = /^\d+$/.test(raw) ? Number(raw) : NaN;
		if (!Number.isInteger(id) || id < 1 || id > TG_APP_ID_MAX) return 'api_id — целое число от 1 до 2147483647';
		if (!/^[0-9a-fA-F]{32}$/.test(appHash.trim())) return 'api_hash — 32 шестнадцатеричных символа';
		return null;
	}

	/** PUT/DELETE `/tg/app`, затем статус входа перечитывается: приложение видно в ответе. */
	async function writeApp(request: () => Promise<unknown>): Promise<void> {
		appBusy = true;
		appError = '';
		try {
			await request();
			await refresh();
		} catch (e) {
			appError = e instanceof ApiFailure ? e.message : String(e);
			// Вход в Telegram при остановленном движке: статус его не покажет — запоминаем отказ.
			if (e instanceof ApiFailure && e.error.kind === 'conflict' && e.error.code === 'tg_logged_in')
				logged = true;
			await refresh();
		} finally {
			appBusy = false;
		}
	}

	function saveApp(e: SubmitEvent) {
		e.preventDefault();
		const invalid = checkApp();
		if (invalid !== null) {
			appError = invalid;
			return;
		}
		const id = Number(appId.trim());
		const hash = appHash.trim();
		// Секрет не хранится в состоянии: значение уходит в запрос, поля очищаются сразу.
		appId = '';
		appHash = '';
		void writeApp(() => call(api.PUT('/tg/app', { body: { api_id: id, api_hash: hash } })));
	}

	function removeApp(): void {
		void writeApp(() => call(api.DELETE('/tg/app')));
	}

	const ERRORS: Record<string, string> = {
		invalid_code: 'Неверный код — попробуйте ещё раз',
		code_expired: 'Код истёк — начните вход заново',
		invalid_password: 'Неверный пароль 2FA — попробуйте ещё раз',
		signup_required: 'Номер не зарегистрирован в Telegram',
		invalid_phone: 'Неверный номер телефона',
		password_required: 'Нужен пароль 2FA',
		unexpected_user: 'Аккаунт привязан к другому пользователю Telegram — сервис вышел из сессии',
		tg_user_taken: 'Этот пользователь Telegram уже привязан к другому аккаунту — сервис вышел из сессии',
		chat_is_self:
			'В настройках чатов указан этот же пользователь Telegram — исправьте настройки и перезапустите аккаунт',
		bind_failed: 'Привязка к пользователю Telegram не сохранилась — бот не вышел в сеть, повторите вход позже',
		session_revoked: 'Сессия Telegram отозвана — войдите снова',
		flood_wait: 'Telegram просит подождать',
		send_code_failed: 'Код не отправлен — Telegram недоступен',
		connect_failed: 'Нет соединения с Telegram',
		online_failed: 'Не удалось выйти в сеть после входа',
		logout_failed: 'Выход из Telegram не удался'
	};

	/** Код ошибки статуса: при отказе из-за чужого пользователя — с тем, к кому аккаунт привязан. */
	function statusError(code: string): string {
		const bound = status?.bound_user_id;
		if (code === 'unexpected_user' && bound) {
			return `Аккаунт привязан к пользователю Telegram ${bound}, а вошёл другой — сервис вышел из сессии. Для другого персонажа создайте новый аккаунт`;
		}
		return ERRORS[code] ?? code;
	}

	async function refresh() {
		try {
			status = await call(api.GET('/tg/status'));
		} catch (e) {
			message = e instanceof ApiFailure ? e.message : String(e);
		}
	}

	// Хост регистрирует движок только после подключения к Telegram: у только что созданного или
	// включённого аккаунта первый ответ — `stopped`. Статус входа перечитывается, когда меняется
	// «движок запущен» (пока аккаунт запускается, статус движка опрашивается раз в 2 с), а не
	// своим опросом.
	const running = $derived(engine.status?.running);
	$effect(() => {
		void running;
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
			} else if (err.kind === 'rate_limited' && err.code !== 'flood_wait') {
				message = errorText(err);
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
		void step(() => call(api.POST('/tg/login/start', { body: { phone: phone.trim() } })));
	};
	const sendCode = (e: SubmitEvent) => {
		e.preventDefault();
		const value = code.trim();
		void step(() => call(api.POST('/tg/login/code', { body: { attempt_id: attempt ?? '', code: value } })));
	};
	const sendPassword = (e: SubmitEvent) => {
		e.preventDefault();
		const value = password;
		void step(() =>
			call(api.POST('/tg/login/password', { body: { attempt_id: attempt ?? '', password: value } }))
		);
	};

	async function logout() {
		const ok = await dialogs.confirm({
			title: 'Выйти из Telegram?',
			body: 'Бот перестанет играть, пока снова не войти (код из Telegram).',
			confirmText: 'Выйти',
			danger: true
		});
		if (ok) await step(() => call(api.POST('/tg/logout')));
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
					{tgStateLabel(status.state)}
				</Pill>
				{#if status.user_id}<span>user_id <span class="font-mono">{status.user_id}</span></span>{/if}
			</p>
			{#if status.state === 'overload'}
				<p class="mt-1 text-sm text-fg-muted">
					Обновлений из Telegram больше, чем бот успевает записать: приём приостановлен. Когда накопленное
					разобрано, бот подключится сам — входить заново не нужно, пропущенное он дочитает из истории чатов.
				</p>
			{/if}
			{#if status.error}
				<p class="ext-text mt-1 text-sm text-bad-fg">{statusError(status.error)}</p>
			{/if}
			{#if status.bound_user_id}
				<p class="mt-2 text-sm text-fg-muted">
					Аккаунт навсегда привязан к пользователю Telegram <span class="font-mono">{status.bound_user_id}</span>.
					Другой персонаж — это новый аккаунт.
				</p>
			{:else if phase !== 'stopped'}
				<p class="mt-2 text-sm text-fg-muted">
					Первый вход навсегда привяжет аккаунт к пользователю Telegram. Другой персонаж — это новый аккаунт.
				</p>
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
				<input
					class="input"
					inputmode="numeric"
					autocomplete="one-time-code"
					name="tg-code"
					bind:value={code}
					required
				/>
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
	{:else if status && phase !== 'overload' && phase !== 'stopped'}
		<form class="card space-y-2" onsubmit={start}>
			<label class="block space-y-1">
				<span class="label">Телефон аккаунта</span>
				<input class="input" type="tel" placeholder="+7…" autocomplete="off" bind:value={phone} required />
			</label>
			<button type="submit" class="btn btn-primary" disabled={busy || !phone.trim()}>Получить код</button>
		</form>
	{/if}

	<section class="card" aria-labelledby="tg-app">
		<h2 id="tg-app" class="card-title">Своё приложение Telegram</h2>
		{#if status === null}
			<p class="text-sm text-fg-muted">…</p>
		{:else}
			<p class="text-sm">
				{app === null || app === 'server' ? 'Серверное приложение' : `Своё: api_id ${app.api_id}`}
			</p>
			{#if appLocked}
				<p class="mt-1 text-sm text-fg-muted">Сначала выйдите из Telegram</p>
			{:else}
				<p class="mt-1 text-sm text-fg-muted">Приложение действует со следующего входа в Telegram.</p>
				<form class="mt-2 space-y-2" onsubmit={saveApp}>
					<label class="block space-y-1">
						<span class="label">api_id</span>
						<input
							class="input"
							type="text"
							inputmode="numeric"
							bind:value={appId}
							autocomplete="off"
							required
						/>
					</label>
					<label class="block space-y-1">
						<span class="label">api_hash</span>
						<input class="input" type="password" autocomplete="new-password" bind:value={appHash} required />
					</label>
					<div class="flex flex-wrap gap-2">
						<button type="submit" class="btn btn-primary" disabled={appBusy || !appId.trim() || !appHash.trim()}>
							Сохранить
						</button>
						{#if app !== 'server'}
							<button type="button" class="btn" disabled={appBusy} onclick={removeApp}>Убрать</button>
						{/if}
					</div>
				</form>
			{/if}
			{#if appError}<p class="ext-text mt-2 text-sm text-warn-fg" role="alert">{appError}</p>{/if}
		{/if}
	</section>
</div>
