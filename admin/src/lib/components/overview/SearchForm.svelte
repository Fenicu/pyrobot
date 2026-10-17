<script lang="ts">
	import type { AccountOut } from '$lib/api/types';
	import { validateQuery } from '$lib/overview/search';
	import { accountTitle } from '$lib/util/game';

	interface Props {
		q: string;
		where: 'db' | 'telegram';
		accountIds: number[];
		limit: number;
		accounts: AccountOut[] | null;
		loading: boolean;
		validationError: string | null;
		onsubmit: (params: { q: string; where: 'db' | 'telegram'; accountIds: number[]; limit: number }) => void;
	}

	let {
		q = '',
		where = 'db',
		accountIds = [],
		limit = 50,
		accounts,
		loading,
		validationError,
		onsubmit
	}: Props = $props();

	let curQ = $state('');
	let curWhere = $state<'db' | 'telegram'>('db');
	let curAccountIds = $state<number[]>([]);
	let curLimit = $state(50);
	let localError = $state<string | null>(null);

	$effect(() => {
		curQ = q;
		curWhere = where;
		curAccountIds = accountIds;
		curLimit = limit;
	});

	function handleSubmit(e: SubmitEvent) {
		e.preventDefault();
		const err = validateQuery(curQ);
		if (err !== null) {
			localError = err;
			return;
		}
		localError = null;
		onsubmit({
			q: curQ.trim(),
			where: curWhere,
			accountIds: curAccountIds,
			limit: curLimit
		});
	}

	const toggleAccount = (id: number) => {
		curAccountIds = curAccountIds.includes(id)
			? curAccountIds.filter((x) => x !== id)
			: [...curAccountIds, id];
	};

	const totalAccounts = $derived(accounts?.length ?? 0);
	const accountsLabel = $derived(
		curAccountIds.length === 0
			? 'Аккаунты: все'
			: `Аккаунты: ${curAccountIds.length} из ${totalAccounts}`
	);
</script>

<div class="space-y-3">
	<form onsubmit={handleSubmit} class="space-y-2 rounded-card border border-line bg-surface p-3 md:p-4">
		<div class="flex flex-col gap-2 sm:flex-row sm:items-center">
			<input
				type="search"
				class="input flex-1"
				placeholder="Текст из игры, например: надел маску"
				bind:value={curQ}
				oninput={() => (localError = null)}
			/>
			<button type="submit" class="btn btn-primary shrink-0" disabled={loading}>
				{loading ? 'Поиск…' : 'Искать'}
			</button>
		</div>

		{#if localError || validationError}
			<p class="text-xs text-bad-fg" role="alert">{localError || validationError}</p>
		{/if}

		<div class="flex flex-wrap items-center gap-2 pt-1 text-xs">
			<div class="inline-flex rounded-ctl border border-line bg-surface-2 p-0.5" role="group" aria-label="Где искать">
				<button
					type="button"
					class="rounded-ctl px-2.5 py-1 text-xs transition-colors {curWhere === 'db'
						? 'bg-accent font-medium text-accent-fg'
						: 'text-fg-muted hover:text-fg'}"
					aria-pressed={curWhere === 'db'}
					onclick={() => (curWhere = 'db')}
				>
					База (90 дней)
				</button>
				<button
					type="button"
					class="rounded-ctl px-2.5 py-1 text-xs transition-colors {curWhere === 'telegram'
						? 'bg-accent font-medium text-accent-fg'
						: 'text-fg-muted hover:text-fg'}"
					aria-pressed={curWhere === 'telegram'}
					onclick={() => (curWhere = 'telegram')}
				>
					Telegram (вся история)
				</button>
			</div>

			{#if accounts && accounts.length > 0}
				<details class="relative rounded-ctl border border-line bg-surface-2 text-xs">
					<summary class="cursor-pointer px-2.5 py-1 text-xs text-fg select-none">{accountsLabel}</summary>
					<div class="absolute top-full left-0 z-20 mt-1 max-h-56 w-56 space-y-1 overflow-y-auto rounded-ctl border border-line bg-surface p-2 shadow-lg">
						<button
							type="button"
							class="btn btn-ghost w-full justify-start text-xs"
							onclick={() => (curAccountIds = [])}
						>
							Все аккаунты
						</button>
						{#each accounts as a (a.id)}
							<label class="flex min-h-7 cursor-pointer items-center gap-2 px-1 text-xs hover:bg-surface-2">
								<input
									type="checkbox"
									checked={curAccountIds.includes(a.id)}
									onchange={() => toggleAccount(a.id)}
								/>
								<span class="ext-text min-w-0">{accountTitle(a)}</span>
							</label>
						{/each}
					</div>
				</details>
			{/if}

			<select
				class="input h-auto w-auto py-1 text-xs"
				bind:value={curLimit}
				aria-label="Лимит на аккаунт"
			>
				<option value={50}>50 на аккаунт</option>
				<option value={100}>100 на аккаунт</option>
				<option value={200}>200 на аккаунт</option>
			</select>
		</div>
	</form>

	{#if loading && curWhere === 'telegram'}
		<p class="card text-sm text-fg-muted" role="status">
			Ищу в Telegram… это может занять до 15 секунд на аккаунт
		</p>
	{/if}
</div>
