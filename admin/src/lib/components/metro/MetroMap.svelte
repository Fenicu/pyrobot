<script lang="ts">
	import { eventTone, EVENT_TEXT, frameAt, type MapModel } from '$lib/metro/model';

	interface Props {
		model: MapModel;
		step: number;
		label: string;
	}
	let { model, step, label }: Props = $props();
	const S = 16;
	const x = (col: number) => (col - model.minCol) * S;
	const y = (row: number) => (row - model.minRow) * S;
	const cx = (col: number) => x(col) + S / 2;
	const cy = (row: number) => y(row) + S / 2;
	const frame = $derived(frameAt(model, step));
	const line = (points: [number, number][]) => points.map(([r, c]) => `${cx(c)},${cy(r)}`).join(' ');
	const done = $derived(line(model.path.slice(0, step + 1)));
	const all = $derived(line(model.path));
	// По одному значку на клетку: последнее событие к текущему шагу, а если их ещё не было —
	// первое будущее (приглушённо).
	const marks = $derived.by(() => {
		const byCell = new Map<string, (typeof model.events)[number]>();
		for (const e of model.events) {
			const key = `${e.pos[0]},${e.pos[1]}`;
			const current = byCell.get(key);
			if (e.step <= step || current === undefined) byCell.set(key, e);
		}
		return [...byCell.values()];
	});
	const fill: Record<string, string> = {
		'#': 'var(--map-wall)',
		'.': 'var(--map-floor)',
		E: 'var(--ok-bg)'
	};
	const tone: Record<string, string> = {
		loot: '#e3b341',
		fight: '#e5484d',
		chest: '#d9822b',
		exit: '#3fb950',
		other: '#a371f7'
	};
</script>

{#if model.cells.length === 0 && model.path.length === 0}
	<p class="rounded-md bg-surface-2 p-4 text-sm text-fg-muted">Карты нет: забег не начат или не записан.</p>
{:else}
	<svg
		viewBox="-2 -2 {model.cols * S + 4} {model.rows * S + 4}"
		class="h-auto w-full max-w-xl rounded-md"
		style:background="var(--map-bg)"
		role="img"
		aria-label={label}
	>
		<title>{label}</title>
		<g aria-hidden="true">
			{#each model.cells as c (`${c.row},${c.col}`)}
				<rect
					x={x(c.col)}
					y={y(c.row)}
					width={S - 1}
					height={S - 1}
					fill={c.visited && c.sym === '.' ? 'var(--map-seen)' : (fill[c.sym] ?? 'var(--muted-bg)')}
					data-cell="{c.row},{c.col}"
					data-sym={c.sym}
				/>
			{/each}
			<polyline points={all} fill="none" stroke="var(--accent)" stroke-opacity="0.25" stroke-width="2" />
			<polyline
				points={done}
				fill="none"
				stroke="var(--accent)"
				stroke-width="2.5"
				stroke-linejoin="round"
				data-role="route"
			/>
			{#each marks as e (`${e.pos[0]},${e.pos[1]}`)}
				<circle
					cx={cx(e.pos[1])}
					cy={cy(e.pos[0])}
					r="4"
					fill={tone[eventTone(e.kind)]}
					opacity={e.step <= step ? 1 : 0.35}
					data-event={e.kind}
					data-pos="{e.pos[0]},{e.pos[1]}"
				>
					<title>{EVENT_TEXT[e.kind] ?? e.kind} · шаг {e.step}</title>
				</circle>
			{/each}
			{#if model.path[0]}
				<text x={cx(model.path[0][1])} y={cy(model.path[0][0]) + 4} text-anchor="middle" font-size="11" font-weight="700" fill="#fff">S</text>
			{/if}
			{#if model.exit}
				<text x={cx(model.exit[1])} y={cy(model.exit[0]) + 4} text-anchor="middle" font-size="11" font-weight="700" fill="#fff">E</text>
			{/if}
			{#if frame.pos}
				<circle
					cx={cx(frame.pos[1])}
					cy={cy(frame.pos[0])}
					r="5.5"
					fill="#fff"
					stroke="var(--accent)"
					stroke-width="2.5"
					data-role="me"
				/>
			{/if}
		</g>
	</svg>
{/if}
