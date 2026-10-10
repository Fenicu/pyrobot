<script lang="ts">
	import History from '@lucide/svelte/icons/history';
	import Search from '@lucide/svelte/icons/search';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { AccountApi } from '$lib/api/account';
	import type { AccountOut } from '$lib/api/types';
	import { errorText } from '$lib/api/errors';
	import type { SettingsEditor } from '$lib/settings/editor.svelte';
	import { settingHelp, settingLabel } from '$lib/settings/labels';
	import { GROUPS, OTHER_CARD_ID, cardOf, changeLabel, placeFields, type MechanicCard } from '$lib/settings/mechanics';
	import { settingNames } from '$lib/settings/names';
	import { settingPaths } from '$lib/settings/paths.svelte';
	import { editable, leaves, pathKey, type Field } from '$lib/settings/schema';
	import { fmtValue } from '$lib/settings/value';
	import { dialogs } from '$lib/stores/confirm.svelte';
	import { toasts } from '$lib/stores/toasts.svelte';
	import Page from '../shell/Page.svelte';
	import HistoryDrawer from './HistoryDrawer.svelte';
	import MechanicCardView from './MechanicCard.svelte';
	import TangerineExchange from './TangerineExchange.svelte';

	interface Props {
		api: AccountApi;
		editor: SettingsEditor;
		/** Движок аккаунта запущен; нет — настройки пишутся прямо в базу и применятся при его старте. */
		running?: boolean;
		now?: Date;
		/** Открытый аккаунт и аккаунты учётки — для «Обмена мандаринами» в карточке «Мандарин». */
		accountId?: number;
		accounts?: AccountOut[] | null;
	}
	let { api, editor, running = true, now, accountId, accounts = null }: Props = $props();
	// Группа — в адресе (?tab=): переживает перезагрузку и переключение аккаунта (switchHref).
	let active = $state<string | null>(page.url.searchParams.get('tab'));
	let query = $state('');
	let historyOpen = $state(false);
	// История перечитывается с каждой новой версией: своё сохранение, перечитывание после чужого
	// изменения или известная чужая версия при несохранённых правках.
	const historyKey = $derived(editor.conflict ?? editor.version ?? 0);
	const replyTo = (v: unknown) => (typeof v === 'number' ? v : null);

	// Карточки колонками по ширине (без выравнивания по строкам): короткая не оставляет пустоту под собой.
	const FLOW = 'columns-[30rem] gap-x-3.5 *:mb-3.5 *:break-inside-avoid';

	const placed = $derived(placeFields(editor.sections));
	const byKey = $derived(
		new Map(editor.sections.flatMap((s) => leaves(editable(s.fields))).map((f) => [pathKey(f.path), f]))
	);
	const featureOf = (card: MechanicCard): Field | null => (card.feature ? (byKey.get(card.feature) ?? null) : null);
	const fieldsOf = (card: MechanicCard): Field[] => placed.get(card.id) ?? [];
	// Карточка видна, если у сервера есть её флаг или хоть одно поле (старый сервер, пустые «Прочие»).
	const shown = (card: MechanicCard) => featureOf(card) !== null || fieldsOf(card).length > 0;
	const groups = $derived(
		GROUPS.map((g) => ({ ...g, cards: g.cards.filter(shown) })).filter((g) => g.cards.length > 0)
	);
	const group = $derived(groups.find((g) => g.id === active) ?? groups[0] ?? null);
	const otherGroup = GROUPS.find((g) => g.cards.some((c) => c.id === OTHER_CARD_ID))?.id;
	const dirtyGroups = $derived(
		new Set(editor.changes.map((p) => cardOf(pathKey(p))?.group.id ?? otherGroup))
	);

	const q = $derived(query.trim().toLowerCase());
	const has = (...text: string[]) => text.some((t) => t.toLowerCase().includes(q));
	// Поиск — по пути, подписи и описанию поля, а также по подписи и описанию его секции и вложенной
	// группы: совпадение у группы находит все её поля. Флаг механики — по пути, названию и справке.
	const hit = (f: Field) => {
		const key = pathKey(f.path);
		if (has(key, settingLabel(key, f.title), settingHelp(key) ?? '')) return true;
		return f.path.slice(0, -1).some((_, i) => {
			const node = pathKey(f.path.slice(0, i + 1));
			return has(settingLabel(node, ''), settingHelp(node) ?? '');
		});
	};
	const found = $derived.by(() => {
		if (!q) return [];
		return groups.flatMap((g) =>
			g.cards.flatMap((card) => {
				const feature = featureOf(card);
				const flag =
					feature !== null && has(pathKey(feature.path), card.title, settingHelp(pathKey(feature.path)) ?? '');
				const fields = fieldsOf(card).filter(hit);
				return flag || fields.length > 0 ? [{ card, feature, fields }] : [];
			})
		);
	});
	const count = $derived(editor.changes.length);
	const nameOf = $derived(settingNames(editor.sections));
	// Что поменяется при сохранении: механика, название, было → станет.
	const pending = $derived(
		editor.changes.map((p) => {
			const key = pathKey(p);
			const name = nameOf(key);
			return {
				key,
				...changeLabel(key, name.section, name.label),
				before: fmtValue(editor.serverValue(p)),
				after: fmtValue(editor.value(p))
			};
		})
	);

	function openGroup(id: string) {
		active = id;
		query = '';
		const url = new URL(page.url);
		url.searchParams.set('tab', id);
		void goto(url.pathname + url.search, { replaceState: true, noScroll: true, keepFocus: true });
	}

	async function save() {
		const result = await editor.save(() =>
			dialogs.confirm({
				title: 'Включить LIVE?',
				body: 'Бот начнёт реально тратить ресурсы: команды действий уйдут в игру.',
				confirmText: 'Сохранить и включить live',
				danger: true
			})
		);
		if (result.ok) {
			toasts.show(`Сохранено, версия ${result.version}`, 'ok');
		} else if ('error' in result && (result.error.kind === 'validation' || result.error.kind === 'out_of_bounds')) {
			toasts.show(
				editor.formErrors.length > 0 ? editor.formErrors.join('\n') : 'Сервер не принял значения — поля подсвечены',
				'error'
			);
		} else if ('error' in result && result.error.kind !== 'version_conflict') {
			toasts.show(errorText(result.error), 'error');
		}
	}
</script>

{#snippet actions()}
	<label class="relative block max-md:w-52">
		<span class="sr-only">Поиск настройки</span>
		<Search class="pointer-events-none absolute top-2 left-2 size-4 text-fg-faint" aria-hidden="true" />
		<input
			class="input min-h-8 py-1 pl-8 md:w-64"
			type="search"
			placeholder="поиск: метро, аптечка, 🍊…"
			title="Ищет по названию, описанию и пути"
			bind:value={query}
		/>
	</label>
	<button type="button" class="btn min-h-8" disabled={!editor.server} onclick={() => (historyOpen = true)}>
		<History class="size-4" aria-hidden="true" />История
	</button>
{/snippet}

{#snippet devPaths(cls: string)}
	<label class="flex items-center gap-2 px-2 text-xs text-fg-faint {cls}">
		<input type="checkbox" checked={settingPaths.show} onchange={(e) => settingPaths.set(e.currentTarget.checked)} />
		пути настроек (для разработчика)
	</label>
{/snippet}

{#snippet tangerine(key: string)}
	{#if key === 'chats.tangerine_reply_to' && accountId !== undefined}
		<TangerineExchange
			{api}
			{accountId}
			{accounts}
			replyTo={replyTo(editor.serverValue(['chats', 'tangerine_reply_to']))}
			onpaired={() => void editor.refresh()}
		/>
	{/if}
{/snippet}

<Page title="Настройки" {actions}>
	{#if editor.loadError && !editor.server}
		<p class="card ext-text text-sm text-bad-fg" role="alert">{errorText(editor.loadError)}</p>
	{:else if !editor.server}
		<p class="text-sm text-fg-muted" role="status">Загрузка…</p>
	{:else}
		{#if editor.conflict !== null}
			<div class="card mb-3 flex flex-wrap items-center gap-2 border-warn-bg text-sm" role="alert">
				<span class="flex-1">
					Настройки уже изменены (версия {editor.conflict}, у вас — {editor.version}). Перечитайте: несохранённые
					правки пропадут.
				</span>
				<button type="button" class="btn" onclick={() => void editor.load()}>Перечитать</button>
			</div>
		{/if}
		{#if editor.restartRequired.length > 0}
			<div class="card mb-3 flex flex-wrap items-center gap-2 text-sm text-warn-fg" role="status">
				{#if editor.restartAccepted}
					<span class="flex-1">Аккаунт перезапускается</span>
				{:else if !running}
					<!-- Перезапускать нечего: движок прочитает настройки при старте. -->
					<span class="flex-1">
						Изменения вступят в силу при включении аккаунта: <span class="font-mono"
							>{editor.restartRequired.join(', ')}</span
						>
					</span>
				{:else}
					<span class="flex-1">
						Изменения вступят в силу после перезапуска аккаунта: <span class="font-mono"
							>{editor.restartRequired.join(', ')}</span
						>
					</span>
					<button type="button" class="btn" disabled={editor.restarting} onclick={() => void editor.restart()}>
						Перезапустить аккаунт
					</button>
				{/if}
				{#if editor.restartError}
					<p class="w-full text-bad-fg" role="alert">{errorText(editor.restartError)}</p>
				{/if}
			</div>
		{/if}

		<div class="grid gap-3 md:grid-cols-[12rem_minmax(0,1fr)]">
			<nav class="min-w-0" aria-label="Группы настроек">
				<ul class="flex gap-1 overflow-x-auto md:flex-col">
					{#each groups as g (g.id)}
						{@const current = group?.id === g.id && !q}
						{#if g.advanced && g.id === groups.find((x) => x.advanced)?.id}
							<li class="shrink-0 border-l border-line md:mt-2 md:border-t md:border-l-0" aria-hidden="true"></li>
						{/if}
						<li class="shrink-0">
							<button
								type="button"
								class="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm whitespace-nowrap hover:bg-surface-2 {current
									? 'bg-accent-soft font-medium'
									: 'text-fg-muted'}"
								aria-current={current ? 'true' : undefined}
								onclick={() => openGroup(g.id)}
							>
								<span class="flex-1">{g.title}{#if dirtyGroups.has(g.id)}<span class="text-accent" title="Есть несохранённые правки" aria-hidden="true"> •</span
									><span class="sr-only">, есть несохранённые правки</span>{/if}</span
								>
								<small class="text-[11px] text-fg-faint tabular-nums" aria-hidden="true">{g.cards.length}</small>
							</button>
						</li>
					{/each}
				</ul>
				{@render devPaths('mt-2 max-md:hidden')}
			</nav>

			{#if q}
				<section class="min-w-0" aria-label="Найденные настройки">
					<div class={FLOW}>
						{#each found as r (r.card.id)}
							<MechanicCardView {editor} card={r.card} feature={r.feature} fields={r.fields} after={tangerine} />
						{:else}
							<p class="text-sm text-fg-muted">Ничего не найдено.</p>
						{/each}
					</div>
				</section>
			{:else if group}
				<section class="min-w-0" aria-label={group.title}>
					<div class={FLOW}>
						{#each group.cards as card (card.id)}
							<MechanicCardView {editor} {card} feature={featureOf(card)} fields={fieldsOf(card)} after={tangerine} />
						{/each}
					</div>
					<!-- На телефоне ленте групп не до флажка: он — в конце «Дополнительно». -->
					{#if group.advanced}{@render devPaths('mt-3 md:hidden')}{/if}
				</section>
			{/if}
		</div>

		{#if count > 0}
			<div
				class="sticky bottom-[calc(4rem_+_env(safe-area-inset-bottom))] z-30 mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-accent bg-accent-soft p-2 text-sm md:bottom-2"
				role="region"
				aria-label="Несохранённые изменения"
			>
				<ul class="max-h-28 w-full basis-full overflow-y-auto text-xs" aria-label="Что поменяется">
					{#each pending as c (c.key)}
						<li class="ext-text">
							{#if c.section}<span class="text-fg-muted">{c.section} ·</span>{/if}
							{c.label}: <span class="text-bad-fg line-through">{c.before}</span> →
							<span class="text-ok-fg">{c.after}</span>
						</li>
					{/each}
				</ul>
				<span class="flex-1">
					{count} {count === 1 ? 'изменение' : count < 5 ? 'изменения' : 'изменений'} · версия {editor.version}
					{#if editor.goesLive}<span class="text-warn-fg"> · включает LIVE</span>{/if}
				</span>
				<button type="button" class="btn" disabled={editor.saving} onclick={() => editor.discard()}>Отменить</button>
				<button type="button" class="btn btn-primary" disabled={editor.saving} onclick={save}>
					{editor.saving ? 'Сохранение…' : 'Сохранить'}
				</button>
			</div>
		{/if}
	{/if}
</Page>

{#if historyOpen && editor.server}
	<HistoryDrawer {api} {editor} refresh={historyKey} {now} onclose={() => (historyOpen = false)} />
{/if}
