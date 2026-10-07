<script lang="ts">
	import { flip } from 'svelte/animate';
	import { prefersReducedMotion } from 'svelte/motion';
	import Icon from './Icon.svelte';
	import type { HostNetwork } from '$lib/apis/state';
	import { locale, t } from '$lib/i18n';

	interface SystemInfo {
		arch: string;
		cpu_count: number;
		cpu_model?: string | null;
		cpu_virtual?: boolean | null;
		cpu_usage?: number;
		memory_total?: number;
		memory_available?: number;
		disk_total?: number;
		disk_used?: number;
		uptime_seconds?: number;
	}

	interface Process {
		pid: number;
		cpu: number;
		mem: number;
		name: string;
	}

	type SortKey = 'cpu' | 'mem';

	interface Props {
		system: SystemInfo;
		processes?: Process[];
		network?: HostNetwork | null;
		networkPending?: boolean;
	}

	let { system: sys, processes = [], network = null, networkPending = false }: Props = $props();

	const COLLAPSED_ROWS = 6;
	// The server sends the top 15 for each sort key (_PROCESS_LIMIT in routers/state.py)
	const EXPANDED_ROWS = 15;

	let sortKey = $state<SortKey>('cpu');
	let expanded = $state(false);

	const sortColumns = $derived<{ key: SortKey; label: string }[]>([
		{ key: 'cpu', label: $t('system.cpuShort') },
		{ key: 'mem', label: $t('system.memoryShort') }
	]);

	const sortedProcesses = $derived.by(() => {
		const tieBreak: SortKey = sortKey === 'cpu' ? 'mem' : 'cpu';
		return [...processes].sort((a, b) => b[sortKey] - a[sortKey] || b[tieBreak] - a[tieBreak]);
	});
	const visibleProcesses = $derived(
		sortedProcesses.slice(0, expanded ? EXPANDED_ROWS : COLLAPSED_ROWS)
	);

	const coresLabel = $derived(
		$t(
			sys.cpu_virtual == null
				? 'system.cores'
				: sys.cpu_virtual
					? 'system.coresVirtual'
					: 'system.coresPhysical',
			{ count: sys.cpu_count }
		)
	);

	const regionName = $derived.by(() => {
		if (!network?.region) return null;
		try {
			const name = new Intl.DisplayNames([$locale], { type: 'region' }).of(network.region);
			return name && name !== network.region ? name : null;
		} catch {
			return null;
		}
	});

	function formatBytes(bytes: number): string {
		if (bytes < 1073741824) return `${(bytes / 1048576).toFixed(0)} MB`;
		return `${(bytes / 1073741824).toFixed(1)} GB`;
	}

	function formatUptime(seconds: number): string {
		const days = Math.floor(seconds / 86400);
		const hours = Math.floor((seconds % 86400) / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		if (days > 0) return $t('system.uptimeDays', { days, hours });
		if (hours > 0) return $t('system.uptimeHours', { hours, minutes });
		return $t('system.uptimeMinutes', { minutes });
	}

	function percentOf(part: number, total: number): number {
		return Math.min(100, Math.max(0, Math.round((part / total) * 100)));
	}
</script>

{#snippet meter(label: string, value: string, percent: number)}
	<div>
		<div class="mb-1 flex items-center justify-between">
			<span class="text-[0.6875rem] text-gray-500 dark:text-gray-500">{label}</span>
			<span class="font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600">{value}</span>
		</div>
		<div class="h-1.5 overflow-hidden rounded-full bg-gray-100 dark:bg-white/6">
			<div
				class="h-full rounded-full bg-gray-400 transition-all dark:bg-gray-500"
				style="width: {percent}%"
			></div>
		</div>
	</div>
{/snippet}

{#snippet pending()}
	<span
		class="inline-block h-2.5 w-20 animate-pulse rounded bg-gray-100 align-middle dark:bg-white/8"
	></span>
{/snippet}

<div class="flex flex-col gap-4">
	<div class="flex flex-col gap-3">
		{#if sys.cpu_usage != null}
			{@render meter($t('system.cpu'), `${sys.cpu_usage}%`, percentOf(sys.cpu_usage, 100))}
		{/if}
		{#if sys.memory_total}
			{@const memUsed = sys.memory_total - (sys.memory_available ?? 0)}
			{@render meter(
				$t('system.memory'),
				`${formatBytes(memUsed)} / ${formatBytes(sys.memory_total)}`,
				percentOf(memUsed, sys.memory_total)
			)}
		{/if}
		{#if sys.disk_total}
			{@render meter(
				$t('system.disk'),
				`${formatBytes(sys.disk_used ?? 0)} / ${formatBytes(sys.disk_total)}`,
				percentOf(sys.disk_used ?? 0, sys.disk_total)
			)}
		{/if}
	</div>

	<dl
		class="divide-y divide-gray-100 rounded-xl border border-gray-100 px-3 text-[0.6875rem] dark:divide-white/6 dark:border-white/6"
	>
		<div class="flex items-start justify-between gap-4 py-2">
			<dt class="shrink-0 text-gray-400 dark:text-gray-500">{$t('system.cpu')}</dt>
			<dd class="flex min-w-0 flex-col items-end gap-1 text-right">
				{#if sys.cpu_model}
					<span class="break-words text-gray-700 dark:text-gray-300">{sys.cpu_model}</span>
				{/if}
				<span
					class="rounded-md bg-gray-100 px-1.5 py-px text-[0.625rem] text-gray-500 dark:bg-white/6 dark:text-gray-400"
					>{coresLabel}</span
				>
			</dd>
		</div>
		<div class="flex items-center justify-between gap-4 py-2">
			<dt class="shrink-0 text-gray-400 dark:text-gray-500">{$t('system.arch')}</dt>
			<dd class="min-w-0 truncate font-mono text-gray-700 dark:text-gray-300">{sys.arch}</dd>
		</div>
		{#if sys.uptime_seconds != null}
			<div class="flex items-center justify-between gap-4 py-2">
				<dt class="shrink-0 text-gray-400 dark:text-gray-500">{$t('system.uptime')}</dt>
				<dd class="min-w-0 truncate text-gray-700 dark:text-gray-300">
					{formatUptime(sys.uptime_seconds)}
				</dd>
			</div>
		{/if}
		<div class="flex items-center justify-between gap-4 py-2">
			<dt class="shrink-0 text-gray-400 dark:text-gray-500">{$t('system.region')}</dt>
			<dd class="flex min-w-0 items-center gap-1.5 text-gray-700 dark:text-gray-300">
				{#if networkPending}
					{@render pending()}
				{:else if network?.region}
					{#if regionName}
						<span class="truncate">{regionName}</span>
					{/if}
					<span
						class="rounded-md bg-gray-100 px-1.5 py-px font-mono text-[0.625rem] text-gray-500 dark:bg-white/6 dark:text-gray-400"
						>{network.region}</span
					>
				{:else}
					<span class="text-gray-400 dark:text-gray-600">—</span>
				{/if}
			</dd>
		</div>
		<div class="flex items-center justify-between gap-4 py-2">
			<dt class="shrink-0 text-gray-400 dark:text-gray-500">IPv4</dt>
			<dd class="flex min-w-0 items-center gap-1.5 text-gray-700 dark:text-gray-300">
				{#if networkPending}
					{@render pending()}
				{:else if network?.ipv4}
					{#if !network.public}
						<span
							class="rounded-md bg-gray-100 px-1.5 py-px text-[0.625rem] text-gray-500 dark:bg-white/6 dark:text-gray-400"
							>{$t('system.privateAddress')}</span
						>
					{/if}
					<span class="truncate font-mono select-all">{network.ipv4}</span>
				{:else}
					<span class="text-gray-400 dark:text-gray-600">—</span>
				{/if}
			</dd>
		</div>
	</dl>

	{#if processes.length}
		<div>
			<table class="w-full table-fixed font-mono text-[0.6875rem]">
				<colgroup>
					<col class="w-16" />
					<col class="w-16" />
					<col />
				</colgroup>
				<thead>
					<tr>
						{#each sortColumns as column (column.key)}
							{@const active = sortKey === column.key}
							<th scope="col" class="pb-1 font-normal" aria-sort={active ? 'descending' : 'none'}>
								<button
									type="button"
									class="group -mr-1 ml-auto flex cursor-pointer items-center gap-0.5 rounded px-1 transition-colors {active
										? 'text-gray-700 dark:text-gray-200'
										: 'text-gray-400 hover:text-gray-600 dark:text-gray-600 dark:hover:text-gray-400'}"
									title={$t('system.sortBy', { column: column.label })}
									onclick={() => (sortKey = column.key)}
								>
									<Icon
										name="chevron-down"
										size={10}
										strokeWidth={2.25}
										class="transition-opacity {active
											? 'opacity-100'
											: 'opacity-0 group-hover:opacity-60'}"
									/>
									{column.label}
								</button>
							</th>
						{/each}
						<th
							scope="col"
							class="pb-1 pl-3 text-left font-normal text-gray-400 dark:text-gray-600"
						>
							{$t('system.process')}
						</th>
					</tr>
				</thead>
				<tbody>
					{#each visibleProcesses as proc (proc.pid)}
						<tr animate:flip={{ duration: prefersReducedMotion.current ? 0 : 180 }}>
							<td
								class="py-px text-right {sortKey === 'cpu'
									? 'text-gray-700 dark:text-gray-300'
									: 'text-gray-500'}">{proc.cpu.toFixed(1)}%</td
							>
							<td
								class="py-px text-right {sortKey === 'mem'
									? 'text-gray-700 dark:text-gray-300'
									: 'text-gray-500'}">{proc.mem.toFixed(1)}%</td
							>
							<td class="truncate py-px pl-3 text-gray-500" title="PID {proc.pid}">{proc.name}</td>
						</tr>
					{/each}
				</tbody>
			</table>

			{#if sortedProcesses.length > COLLAPSED_ROWS}
				<button
					type="button"
					class="mt-1.5 flex w-full cursor-pointer items-center justify-center gap-1 rounded-lg py-1 text-[0.6875rem] text-gray-400 transition-colors hover:bg-gray-50 hover:text-gray-600 dark:text-gray-600 dark:hover:bg-white/4 dark:hover:text-gray-400"
					aria-expanded={expanded}
					onclick={() => (expanded = !expanded)}
				>
					<Icon name={expanded ? 'chevron-up' : 'chevron-down'} size={11} />
					{expanded ? $t('system.showLess') : $t('system.showMore')}
				</button>
			{/if}
		</div>
	{/if}
</div>
