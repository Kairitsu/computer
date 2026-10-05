<script lang="ts" module>
	export type HeatmapMode = 'daily' | 'weekly' | 'cumulative';
</script>

<script lang="ts">
	/**
	 * Contribution-style activity heatmap after Grok App: GitHub greens,
	 * Sunday-first week columns that fit the card, month labels on top,
	 * Mon/Wed/Fri on the left and a hover tip.
	 *
	 * daily: one cell per day · weekly: one bar per week column ·
	 * cumulative: days colored by the running token total.
	 */
	import type { UsageHeatmapEntry } from '$lib/apis/chat';
	import { locale, t } from '$lib/i18n';
	import { formatCount } from '$lib/utils/usageFormat';

	interface Props {
		days: UsageHeatmapEntry[];
		mode: HeatmapMode;
	}

	let { days, mode }: Props = $props();

	type Cell = {
		date: string | null;
		label: string;
		tokens: number;
		messages: number;
		cumulative: number | null;
		value: number;
		level: number;
	};

	const GAP = 3;
	const LABEL_COL = 22;
	const RIGHT_PAD = 12;
	const MIN_CELL = 10;
	const MAX_CELL = 14;
	const WEEK_CELL_H = 28;
	const MAX_WEEKS = 53;
	const MIN_MONTH_GAP = 3;

	let width = $state(0);
	let hover = $state<{ cell: Cell; left: number; top: number; above: boolean } | null>(null);

	const useDayGrid = $derived(mode !== 'weekly');
	const labelCol = $derived(useDayGrid ? LABEL_COL : 0);
	/** Agents that report no token usage still show activity by message count. */
	const metric = $derived<'tokens' | 'messages'>(
		days.some((day) => day.tokens > 0) ? 'tokens' : 'messages'
	);
	const weekCount = $derived(
		width
			? Math.max(
					4,
					Math.min(MAX_WEEKS, Math.floor((width - labelCol - RIGHT_PAD + GAP) / (MIN_CELL + GAP)))
				)
			: MAX_WEEKS
	);
	const weeks = $derived(buildWeeks(days, weekCount, mode, metric));
	const columns = $derived(weeks.length);
	const cell = $derived(
		width && columns
			? Math.max(
					MIN_CELL,
					Math.min(
						MAX_CELL,
						Math.floor((width - labelCol - RIGHT_PAD - (columns - 1) * GAP) / columns)
					)
				)
			: MIN_CELL
	);
	const graphWidth = $derived(columns * (cell + GAP) - GAP);
	const graphHeight = $derived(useDayGrid ? 7 * (cell + GAP) - GAP : WEEK_CELL_H);
	const monthLabels = $derived(buildMonthLabels(weeks));
	const monthFormat = $derived(safeFormat($locale, { month: 'short' }));
	const weekdayLabels = $derived.by(() => {
		const tight = /^(zh|ja|ko)\b/i.test($locale);
		const format = safeFormat($locale, { weekday: tight ? 'narrow' : 'short' });
		// 2023-01-01 was a Sunday.
		return Array.from({ length: 7 }, (_, i) => format.format(new Date(2023, 0, 1 + i)));
	});
	const levelColors = [0, 1, 2, 3, 4].map((level) => `var(--heatmap-${level})`);

	function safeFormat(code: string, options: Intl.DateTimeFormatOptions) {
		try {
			return new Intl.DateTimeFormat(code, options);
		} catch {
			return new Intl.DateTimeFormat('en', options);
		}
	}

	function parseYmd(value: string) {
		const [y, m, d] = value.split('-').map(Number);
		return new Date(y, m - 1, d);
	}

	function ymd(date: Date) {
		const m = `${date.getMonth() + 1}`.padStart(2, '0');
		const d = `${date.getDate()}`.padStart(2, '0');
		return `${date.getFullYear()}-${m}-${d}`;
	}

	/** Quartiles of the positive values → levels 1–4. */
	function levelOf(value: number, thresholds: number[]) {
		if (value <= 0) return 0;
		if (value <= thresholds[0]) return 1;
		if (value <= thresholds[1]) return 2;
		if (value <= thresholds[2]) return 3;
		return 4;
	}

	function thresholdsFor(values: number[]) {
		const positive = values.filter((value) => value > 0).sort((a, b) => a - b);
		if (!positive.length) return [1, 2, 3];
		const at = (p: number) => positive[Math.floor(p * (positive.length - 1))];
		const t1 = at(0.25);
		const t2 = Math.max(at(0.5), t1);
		return [t1, t2, Math.max(at(0.75), t2)];
	}

	function buildWeeks(
		data: UsageHeatmapEntry[],
		count: number,
		heatMode: HeatmapMode,
		valueOf: 'tokens' | 'messages'
	): Cell[][] {
		if (!data.length) return [];
		const byDate = new Map(data.map((entry) => [entry.date, entry]));
		const running = new Map<string, number>();
		let total = 0;
		for (const entry of data) {
			total += entry[valueOf];
			running.set(entry.date, total);
		}

		const first = parseYmd(data[0].date);
		const last = parseYmd(data[data.length - 1].date);
		const end = new Date(last);
		end.setDate(end.getDate() + (6 - end.getDay()));
		const start = new Date(end);
		start.setDate(start.getDate() - (count * 7 - 1));

		const cells: Cell[] = [];
		for (const cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) {
			const key = ymd(cursor);
			const inRange = cursor >= first && cursor <= last;
			const entry = inRange ? byDate.get(key) : undefined;
			const cumulative = inRange ? (running.get(key) ?? null) : null;
			const own = entry?.[valueOf] ?? 0;
			cells.push({
				date: inRange ? key : null,
				label: key,
				tokens: entry?.tokens ?? 0,
				messages: entry?.messages ?? 0,
				cumulative: heatMode === 'cumulative' ? cumulative : null,
				value: heatMode === 'cumulative' ? (cumulative ?? 0) : own,
				level: 0
			});
		}

		let grouped: Cell[][] = [];
		for (let i = 0; i < cells.length; i += 7) grouped.push(cells.slice(i, i + 7));

		if (heatMode === 'weekly') {
			grouped = grouped.map((week) => {
				const inRange = week.filter((day) => day.date);
				const startLabel = inRange[0]?.label ?? week[0].label;
				const endLabel = inRange[inRange.length - 1]?.label ?? week[6].label;
				const sum = (key: 'tokens' | 'messages') => inRange.reduce((acc, day) => acc + day[key], 0);
				return [
					{
						date: inRange.length ? startLabel : null,
						label:
							startLabel === endLabel
								? startLabel
								: `${startLabel} – ${endLabel.slice(startLabel.slice(0, 4) === endLabel.slice(0, 4) ? 5 : 0)}`,
						tokens: sum('tokens'),
						messages: sum('messages'),
						cumulative: null,
						value: sum(valueOf),
						level: 0
					}
				];
			});
		}

		const thresholds = thresholdsFor(
			grouped.flat().flatMap((item) => (item.date ? [item.value] : []))
		);
		for (const item of grouped.flat()) {
			item.level = item.date ? levelOf(item.value, thresholds) : 0;
		}
		return grouped;
	}

	function buildMonthLabels(columnsData: Cell[][]) {
		const labels: { column: number; date: Date }[] = [];
		let lastMonth = -1;
		let lastColumn = -MIN_MONTH_GAP;
		columnsData.forEach((week, column) => {
			// `date` is a plain day even for week bars, whose label is a range.
			const sample = week.find((day) => day.date)?.date;
			if (!sample) return;
			const date = parseYmd(sample);
			if (date.getMonth() === lastMonth) return;
			lastMonth = date.getMonth();
			if (column - lastColumn < MIN_MONTH_GAP) return;
			labels.push({ column, date });
			lastColumn = column;
		});
		return labels;
	}

	function showTip(event: PointerEvent, item: Cell) {
		if (!item.date) return;
		const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
		const tipWidth = 184;
		const above = rect.top - 84 >= 8;
		const left = Math.min(
			Math.max(8, rect.left + rect.width / 2 - tipWidth / 2),
			Math.max(8, window.innerWidth - tipWidth - 8)
		);
		hover = { cell: item, left, top: above ? rect.top - 6 : rect.bottom + 6, above };
	}

	function ariaFor(item: Cell) {
		return `${item.label}, ${$t('usage.tokensLabel')} ${formatCount(item.tokens, $locale)}`;
	}
</script>

<svelte:window onscrollcapture={() => (hover = null)} />

<div class="heatmap" bind:clientWidth={width}>
	{#if columns}
		<div class="heatmap-inner" style="width: {labelCol + graphWidth}px;">
			<div class="heatmap-months" style="margin-left: {labelCol}px;">
				{#each monthLabels as month (month.column)}
					<span style="left: {month.column * (cell + GAP)}px;"
						>{monthFormat.format(month.date)}</span
					>
				{/each}
			</div>

			<div class="heatmap-body">
				{#if useDayGrid}
					<div class="heatmap-dow" style="width: {LABEL_COL}px; gap: {GAP}px;">
						{#each weekdayLabels as label, index}
							<span style="height: {cell}px; visibility: {index % 2 ? 'visible' : 'hidden'};"
								>{label}</span
							>
						{/each}
					</div>
				{/if}
				<div
					class="heatmap-grid"
					role="group"
					aria-label={$t('usage.heatmap')}
					style="grid-template-columns: repeat({columns}, {cell}px); grid-template-rows: {useDayGrid
						? `repeat(7, ${cell}px)`
						: `${WEEK_CELL_H}px`}; gap: {GAP}px; width: {graphWidth}px; height: {graphHeight}px;"
					onpointerleave={() => (hover = null)}
				>
					{#each weeks as week, column}
						{#each week as item, row}
							<div
								role={item.date ? 'img' : 'presentation'}
								class="heatmap-cell"
								class:is-empty={!item.date}
								class:is-week={!useDayGrid}
								aria-label={item.date ? ariaFor(item) : undefined}
								style="grid-column: {column + 1}; grid-row: {row + 1}; background-color: {item.date
									? levelColors[item.level]
									: 'transparent'};"
								onpointerenter={(event) => showTip(event, item)}
							></div>
						{/each}
					{/each}
				</div>
			</div>

			<div class="heatmap-legend">
				<span>{$t('usage.less')}</span>
				{#each levelColors as color}
					<span
						class="heatmap-cell"
						style="width: {cell}px; height: {cell}px; background-color: {color};"
					></span>
				{/each}
				<span>{$t('usage.more')}</span>
			</div>
		</div>
	{/if}
</div>

{#if hover}
	<div
		class="heatmap-tip"
		class:above={hover.above}
		role="tooltip"
		style="left: {hover.left}px; top: {hover.top}px;"
	>
		<div class="heatmap-tip-date">{hover.cell.label}</div>
		<div class="heatmap-tip-row">
			<span>{$t('usage.tokensLabel')}</span>
			<span>{formatCount(hover.cell.tokens, $locale)}</span>
		</div>
		<div class="heatmap-tip-row">
			<span>{$t('usage.messagesLabel')}</span>
			<span>{hover.cell.messages.toLocaleString($locale)}</span>
		</div>
		{#if hover.cell.cumulative != null}
			<div class="heatmap-tip-row">
				<span>{$t('usage.cumulativeLabel')}</span>
				<span>{formatCount(hover.cell.cumulative, $locale)}</span>
			</div>
		{/if}
	</div>
{/if}

<style>
	.heatmap {
		--heatmap-0: #ebedf0;
		--heatmap-1: #9be9a8;
		--heatmap-2: #40c463;
		--heatmap-3: #30a14e;
		--heatmap-4: #216e39;
		position: relative;
		width: 100%;
		min-width: 0;
		user-select: none;
	}

	:global(.dark) .heatmap {
		--heatmap-0: rgba(255, 255, 255, 0.08);
		--heatmap-1: #0e4429;
		--heatmap-2: #006d32;
		--heatmap-3: #26a641;
		--heatmap-4: #39d353;
	}

	.heatmap-inner {
		margin: 0 auto;
	}

	.heatmap-months {
		position: relative;
		height: 1rem;
	}

	.heatmap-months span {
		position: absolute;
		top: 1px;
		font-size: 0.625rem;
		line-height: 1;
		white-space: nowrap;
		color: var(--app-fg-subtle);
	}

	.heatmap-body {
		display: flex;
		align-items: flex-start;
	}

	.heatmap-dow {
		display: flex;
		flex-direction: column;
		flex-shrink: 0;
		font-size: 0.625rem;
		line-height: 1;
		color: var(--app-fg-subtle);
	}

	.heatmap-dow span {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		padding-right: 0.25rem;
	}

	.heatmap-grid {
		display: grid;
	}

	.heatmap-cell {
		border-radius: 2px;
		border: 1px solid color-mix(in oklab, var(--app-fg) 9%, transparent);
		box-sizing: border-box;
		transition:
			box-shadow 0.1s ease,
			border-color 0.1s ease;
	}

	.heatmap-cell.is-week {
		border-radius: 3px;
	}

	.heatmap-cell.is-empty {
		opacity: 0;
		pointer-events: none;
	}

	.heatmap-grid .heatmap-cell:not(.is-empty):hover {
		border-color: color-mix(in oklab, var(--app-fg) 22%, transparent);
		box-shadow: 0 0 0 1px color-mix(in oklab, var(--app-fg) 16%, transparent);
	}

	.heatmap-legend {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 4px;
		margin-top: 0.625rem;
		font-size: 0.625rem;
		color: var(--app-fg-subtle);
	}

	.heatmap-tip {
		position: fixed;
		z-index: 200;
		width: 184px;
		padding: 0.4375rem 0.625rem;
		border-radius: 0.5rem;
		border: 1px solid color-mix(in oklab, var(--app-fg) 10%, transparent);
		background: color-mix(in oklab, var(--app-bg) 92%, transparent);
		backdrop-filter: blur(16px) saturate(1.4);
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14);
		color: var(--app-fg);
		font-size: 0.75rem;
		pointer-events: none;
		animation: tip-in 0.08s ease-out;
	}

	.heatmap-tip.above {
		transform: translateY(-100%);
	}

	.heatmap-tip-date {
		margin-bottom: 0.25rem;
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}

	.heatmap-tip-row {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		margin-top: 0.125rem;
		color: var(--app-fg-muted);
	}

	.heatmap-tip-row span:last-child {
		color: var(--app-fg);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}

	@keyframes tip-in {
		from {
			opacity: 0.5;
		}
	}
</style>
