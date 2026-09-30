<script lang="ts">
	import { cellKey, cellMarks, type MapModel } from '$lib/metro/model';

	interface Props {
		model: MapModel;
		step: number;
		label: string;
	}
	let { model, step, label }: Props = $props();
	const uid = $props.id();
	/** Клетка и ширина тоннеля: между соседними проходами — полоса камня. */
	const S = 16;
	const T = 11;
	/** Сколько последних шагов пути — ярким следом. */
	const TRAIL = 12;
	const cx = (col: number) => (col - model.minCol) * S + S / 2;
	const cy = (row: number) => (row - model.minRow) * S + S / 2;
	const at = (key: string, when: Map<string, number>) => (when.get(key) ?? Infinity) <= step;

	// Туман: до конца забега видно только то, что уже попало в окно кадра; пройденное — светлее.
	const tunnels = $derived.by(() => {
		const full = step >= model.path.length - 1;
		return model.tunnels
			.map((c) => ({ ...c, key: cellKey(c.row, c.col) }))
			.filter((c) => full || at(c.key, model.seenAt))
			.map((c) => ({ ...c, walked: at(c.key, model.visitedAt) }));
	});
	const links = $derived.by(() => {
		const byKey = new Map(tunnels.map((c) => [c.key, c]));
		return tunnels.flatMap((a) =>
			[byKey.get(cellKey(a.row, a.col + 1)), byKey.get(cellKey(a.row + 1, a.col))]
				.filter((b) => b !== undefined)
				.map((b) => ({ a, b, key: `${a.key}-${b.key}`, walked: a.walked && b.walked }))
		);
	});
	const fill = (walked: boolean) => (walked ? 'var(--map-walked)' : 'var(--map-tunnel)');

	const points = (from: number) =>
		model.path
			.slice(Math.max(0, from), step + 1)
			.map(([r, c]) => `${cx(c)},${cy(r)}`)
			.join(' ');
	const marks = $derived(cellMarks(model, step));
	const me = $derived(model.path[Math.min(step, model.path.length - 1)] ?? null);
	/** Куда смотрит персонаж: направление последнего хода, в градусах. */
	const heading = $derived.by(() => {
		const prev = step > 0 ? model.path[Math.min(step, model.path.length - 1) - 1] : undefined;
		if (!me || !prev || (prev[0] === me[0] && prev[1] === me[1])) return null;
		return (Math.atan2(me[0] - prev[0], me[1] - prev[1]) * 180) / Math.PI;
	});
	const exitKnown = $derived(
		model.exit !== null && (step >= model.path.length - 1 || at(cellKey(...model.exit), model.seenAt))
	);
</script>

{#if model.tunnels.length === 0}
	<p class="rounded-md bg-surface-2 p-4 text-sm text-fg-muted">Карты нет: забег не начат или не записан.</p>
{:else}
	<svg
		viewBox="0 0 {model.cols * S} {model.rows * S}"
		class="h-auto max-h-[75vh] w-full max-w-xl"
		role="img"
		aria-label={label}
	>
		<title>{label}</title>
		<defs>
			<!-- Светлая обводка значков: тёмные (🕳) видны и на тёмном тоннеле. -->
			<filter id="halo-{uid}">
				<feMorphology in="SourceAlpha" operator="dilate" radius="0.6" result="edge" />
				<feFlood style:flood-color="var(--map-halo)" />
				<feComposite in2="edge" operator="in" />
				<feMerge><feMergeNode /><feMergeNode in="SourceGraphic" /></feMerge>
			</filter>
		</defs>
		<g aria-hidden="true">
			<rect width={model.cols * S} height={model.rows * S} rx="6" fill="var(--map-rock)" />
			<g data-role="tunnels">
				{#each links as l (l.key)}
					<line x1={cx(l.a.col)} y1={cy(l.a.row)} x2={cx(l.b.col)} y2={cy(l.b.row)} stroke={fill(l.walked)} stroke-width={T} />
				{/each}
				{#each tunnels as c (c.key)}
					<rect
						x={cx(c.col) - T / 2}
						y={cy(c.row) - T / 2}
						width={T}
						height={T}
						rx="3"
						fill={c.sym === 'E' ? 'var(--ok-bg)' : fill(c.walked)}
						data-cell={c.key}
						data-sym={c.sym}
						data-walked={c.walked || undefined}
					/>
				{/each}
			</g>
			<polyline
				points={points(0)}
				fill="none"
				stroke="var(--accent)"
				stroke-opacity="0.45"
				stroke-width="1.5"
				stroke-linejoin="round"
				data-role="route"
			/>
			<polyline
				points={points(step - TRAIL)}
				fill="none"
				stroke="var(--accent)"
				stroke-width="3"
				stroke-linecap="round"
				stroke-linejoin="round"
				data-role="trail"
			/>
			<g font-size="10" text-anchor="middle" dominant-baseline="central" filter="url(#halo-{uid})">
				{#if model.path[0]}
					<text x={cx(model.path[0][1])} y={cy(model.path[0][0])} data-role="entrance">🚇</text>
				{/if}
				{#if model.exit && exitKnown}
					<text x={cx(model.exit[1])} y={cy(model.exit[0])} data-role="exit">🚪</text>
				{/if}
				{#each marks as m (cellKey(...m.pos))}
					<g data-event={m.kind} data-pos={cellKey(...m.pos)} opacity={m.dim ? 0.45 : undefined}>
						<title>{m.title}</title>
						<text x={cx(m.pos[1])} y={cy(m.pos[0])}>{m.icon}</text>
					</g>
				{/each}
			</g>
			{#if me}
				<g transform="translate({cx(me[1])} {cy(me[0])}) rotate({heading ?? 0})" data-role="me">
					<circle r="7.5" fill="var(--accent)" fill-opacity="0.3" />
					{#if heading !== null}<path d="M6 -3.5 L10 0 L6 3.5 Z" fill="var(--accent)" />{/if}
					<circle r="4.5" fill="#fff" stroke="var(--accent)" stroke-width="2" />
				</g>
			{/if}
		</g>
	</svg>
{/if}
