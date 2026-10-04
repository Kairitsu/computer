<script lang="ts">
	/**
	 * Composer chip with the current context fullness and subscription quota.
	 * Click for details: context tokens + compaction, SuperGrok or local budget.
	 */
	import type { ContextUsage } from '$lib/apis/chat';
	import { formatTokens, quotaLoading, quotaSummary, refreshQuota } from '$lib/stores/quota';
	import { i18next, t } from '$lib/i18n';
	import Icon from '../Icon.svelte';
	import Popover from '../common/Popover.svelte';

	interface Props {
		contextUsage?: ContextUsage | null;
		/** Context window chosen in the composer (null = model default). */
		contextWindow?: number | null;
		/** Model default context window, shown before the first reply. */
		defaultContextWindow?: number;
		hasChatContent?: boolean;
		oncompact?: () => void;
	}

	let {
		contextUsage = null,
		contextWindow = null,
		defaultContextWindow,
		hasChatContent = false,
		oncompact
	}: Props = $props();

	const RING = 2 * Math.PI * 8;

	let btnEl: HTMLButtonElement | undefined = $state();
	let open = $state(false);

	const limit = $derived(contextUsage?.threshold || contextWindow || defaultContextWindow || 0);
	const contextPercent = $derived(Math.max(0, Math.round(contextUsage?.percent ?? 0)));
	const ringOffset = $derived(RING * (1 - Math.min(contextPercent, 100) / 100));
	const quotaRemaining = $derived(
		$quotaSummary.source === 'none' ? null : Math.round($quotaSummary.remainingPercent)
	);
	const tone = (percent: number) => (percent >= 90 ? 'danger' : percent >= 70 ? 'warn' : 'ok');

	function formatReset(seconds: number | null) {
		if (!seconds) return '';
		const date = new Date(seconds * 1000).toLocaleString(i18next.language, {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		});
		const hours = (seconds * 1000 - Date.now()) / 3_600_000;
		if (hours <= 0) return date;
		const relative = new Intl.RelativeTimeFormat(i18next.language, { numeric: 'auto' });
		const label =
			hours >= 24
				? relative.format(Math.round(hours / 24), 'day')
				: relative.format(Math.max(1, Math.round(hours)), 'hour');
		return `${date} · ${label}`;
	}

	function openPanel() {
		open = !open;
		if (open) void refreshQuota();
	}
</script>

<button
	bind:this={btnEl}
	type="button"
	class="usage-chip"
	aria-haspopup="dialog"
	aria-expanded={open}
	title={$t('usageIndicator.title')}
	onclick={openPanel}
>
	<svg class="size-3.5 shrink-0 -rotate-90" viewBox="0 0 20 20" aria-hidden="true">
		<circle cx="10" cy="10" r="8" fill="none" stroke-width="2.5" class="ring-track" />
		<circle
			cx="10"
			cy="10"
			r="8"
			fill="none"
			stroke-width="2.5"
			stroke-linecap="round"
			class="ring-value {tone(contextPercent)}"
			style="stroke-dasharray: {RING}; stroke-dashoffset: {ringOffset};"
		/>
	</svg>
	{#if contextUsage}
		<span class="tabular-nums">{contextPercent}%</span>
	{/if}
	{#if quotaRemaining !== null}
		{#if contextUsage}<span class="usage-sep" aria-hidden="true"></span>{/if}
		<span class="tabular-nums">{$t('usageIndicator.quotaShort', { percent: quotaRemaining })}</span>
	{/if}
</button>

{#if open && btnEl}
	<Popover anchor={btnEl} align="end" width="17rem" onclose={() => (open = false)}>
		<div class="flex flex-col gap-2.5 p-1.5">
			<section>
				<div class="flex items-center justify-between">
					<h4 class="usage-title">{$t('usageIndicator.context')}</h4>
					<span class="text-xs tabular-nums font-medium">
						{contextUsage ? `${contextPercent}%` : '—'}
					</span>
				</div>
				<div class="usage-bar">
					<span class={tone(contextPercent)} style="width: {Math.min(contextPercent, 100)}%;"
					></span>
				</div>
				<p class="usage-meta tabular-nums">
					{#if contextUsage}
						{$t('usageIndicator.contextTokens', {
							used: formatTokens(contextUsage.tokens),
							limit: formatTokens(limit)
						})}
					{:else if limit}
						{$t('usageIndicator.contextEmpty', { limit: formatTokens(limit) })}
					{:else}
						{$t('usageIndicator.contextNone')}
					{/if}
				</p>
				<p class="usage-meta">{$t('usageIndicator.contextHint')}</p>
				{#if oncompact && hasChatContent}
					<button
						type="button"
						class="usage-action mt-1.5"
						onclick={() => {
							open = false;
							oncompact?.();
						}}
					>
						{$t('usageIndicator.compact')}
					</button>
				{/if}
			</section>

			<div class="app-divider h-px"></div>

			<section>
				<div class="flex items-center justify-between gap-2">
					<h4 class="usage-title">{$t('usageIndicator.quota')}</h4>
					<button
						type="button"
						class="usage-icon-btn"
						aria-label={$t('usageIndicator.refresh')}
						title={$t('usageIndicator.refresh')}
						onclick={() => refreshQuota(true)}
					>
						<span class:animate-spin={$quotaLoading} class="flex">
							<Icon name="refresh" size={12} />
						</span>
					</button>
				</div>

				{#if $quotaSummary.source === 'supergrok'}
					{@const summary = $quotaSummary}
					<div class="flex items-baseline justify-between">
						<span class="usage-meta">SuperGrok</span>
						<span class="text-xs tabular-nums font-medium">
							{$t('usageIndicator.remaining', { percent: Math.round(summary.remainingPercent) })}
						</span>
					</div>
					<div class="usage-bar">
						<span
							class={tone(summary.usedPercent)}
							style="width: {Math.min(summary.usedPercent, 100)}%;"
						></span>
					</div>
					{#each summary.supergrok.products as product (product.id)}
						<div class="usage-meta flex justify-between tabular-nums">
							<span>{product.label}</span>
							<span>{$t('usageIndicator.used', { percent: Math.round(product.used_percent) })}</span
							>
						</div>
					{/each}
					{#if summary.resetsAt}
						<p class="usage-meta">
							{$t('usageIndicator.resetsAt', { time: formatReset(summary.resetsAt) })}
						</p>
					{/if}
				{:else if $quotaSummary.source === 'budget'}
					{@const summary = $quotaSummary}
					<div class="flex items-baseline justify-between">
						<span class="usage-meta">{$t(`usageIndicator.period.${summary.period}`)}</span>
						<span class="text-xs tabular-nums font-medium">
							{$t('usageIndicator.remaining', { percent: Math.round(summary.remainingPercent) })}
						</span>
					</div>
					<div class="usage-bar">
						<span
							class={tone(summary.usedPercent)}
							style="width: {Math.min(summary.usedPercent, 100)}%;"
						></span>
					</div>
					<p class="usage-meta tabular-nums">
						{$t('usageIndicator.budgetTokens', {
							used: formatTokens(summary.tokensUsed),
							budget: formatTokens(summary.budget)
						})}
					</p>
					<p class="usage-meta">
						{$t('usageIndicator.resetsAt', { time: formatReset(summary.resetsAt) })}
					</p>
				{:else}
					<p class="usage-meta">{$t('usageIndicator.noQuota')}</p>
					{#if $quotaSummary.error}
						<p class="usage-meta text-amber-600 dark:text-amber-400 break-words">
							{$t('usageIndicator.supergrokError')}
						</p>
					{/if}
					{#if $quotaSummary.tokensUsed !== null}
						<p class="usage-meta tabular-nums">
							{$t(`usageIndicator.period.${$quotaSummary.period}`)} · {formatTokens(
								$quotaSummary.tokensUsed
							)} tokens
						</p>
					{/if}
				{/if}
				<button
					type="button"
					class="usage-link mt-1.5"
					onclick={() => {
						open = false;
						window.dispatchEvent(
							new CustomEvent('cptr:open-settings', { detail: { tab: 'usage' } })
						);
					}}
				>
					{$t('usageIndicator.configure')}
				</button>
			</section>
		</div>
	</Popover>
{/if}

<style>
	.usage-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3125rem;
		height: 1.875rem;
		padding: 0 0.625rem;
		border-radius: 0.75rem;
		border: 1px solid color-mix(in oklab, var(--app-fg) 10%, transparent);
		background: var(--app-bg);
		color: var(--app-fg-muted);
		font-size: 0.6875rem;
		font-weight: 500;
		box-shadow: 0 1px 2px color-mix(in oklab, black 5%, transparent);
		transition:
			border-color 0.1s,
			color 0.1s;
	}

	.usage-chip:hover {
		color: var(--app-fg);
		border-color: color-mix(in oklab, var(--app-fg) 20%, transparent);
	}

	.usage-sep {
		width: 1px;
		height: 0.75rem;
		background: color-mix(in oklab, var(--app-fg) 15%, transparent);
	}

	.ring-track {
		stroke: color-mix(in oklab, var(--app-fg) 14%, transparent);
	}

	.ring-value {
		stroke: var(--app-accent);
		transition: stroke-dashoffset 0.3s;
	}

	.ring-value.warn {
		stroke: #d97706;
	}

	.ring-value.danger {
		stroke: #dc2626;
	}

	.usage-title {
		font-size: 0.75rem;
		font-weight: 600;
		color: var(--app-fg);
	}

	.usage-meta {
		margin-top: 0.25rem;
		font-size: 0.6875rem;
		line-height: 1.35;
		color: var(--app-fg-subtle);
	}

	.usage-bar {
		margin-top: 0.375rem;
		height: 0.3125rem;
		overflow: hidden;
		border-radius: 999px;
		background: color-mix(in oklab, var(--app-fg) 10%, transparent);
	}

	.usage-bar span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: var(--app-accent);
		transition: width 0.3s;
	}

	.usage-bar span.warn {
		background: #d97706;
	}

	.usage-bar span.danger {
		background: #dc2626;
	}

	.usage-action {
		height: 1.625rem;
		padding: 0 0.625rem;
		border-radius: 0.5rem;
		font-size: 0.6875rem;
		font-weight: 500;
		background: var(--app-accent);
		color: var(--app-accent-fg);
	}

	.usage-link {
		font-size: 0.6875rem;
		color: var(--app-accent);
	}

	.usage-link:hover {
		text-decoration: underline;
	}

	.usage-icon-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.375rem;
		height: 1.375rem;
		border-radius: 0.375rem;
		color: var(--app-fg-subtle);
	}

	.usage-icon-btn:hover {
		background: var(--app-hover);
		color: var(--app-fg);
	}
</style>
