<script lang="ts">
	/**
	 * Settings → Usage: subscription quota. Shows the SuperGrok quota when the
	 * Grok CLI is signed in, and edits the local token budget used otherwise.
	 */
	import { onMount } from 'svelte';
	import { quotaBudget, type QuotaPeriod } from '$lib/stores';
	import {
		formatTokens,
		isSuperGrokQuota,
		quotaData,
		quotaLoading,
		quotaSummary,
		refreshQuota
	} from '$lib/stores/quota';
	import { t } from '$lib/i18n';
	import Icon from '../Icon.svelte';

	const periods: QuotaPeriod[] = ['day', 'week', 'month'];

	let budgetDraft = $state('');

	$effect(() => {
		budgetDraft = $quotaBudget.tokens ? String($quotaBudget.tokens) : '';
	});

	const supergrok = $derived($quotaData?.supergrok ?? null);

	onMount(() => {
		void refreshQuota();
	});

	/** "5m" / "500k" / "2000000" → tokens. */
	function parseTokens(raw: string): number | null {
		const match = /^\s*(\d+(?:\.\d+)?)\s*([km]?)\s*$/i.exec(raw);
		if (!match) return null;
		const unit = match[2].toLowerCase();
		const value = Number(match[1]) * (unit === 'm' ? 1_000_000 : unit === 'k' ? 1_000 : 1);
		return value > 0 ? Math.round(value) : null;
	}

	function setPeriod(period: QuotaPeriod) {
		quotaBudget.update((budget) => ({ ...budget, period }));
	}

	function saveBudget() {
		quotaBudget.update((budget) => ({ ...budget, tokens: parseTokens(budgetDraft) }));
	}

	function clearBudget() {
		budgetDraft = '';
		quotaBudget.update((budget) => ({ ...budget, tokens: null }));
	}
</script>

<section class="quota-card mb-5">
	<div class="flex items-center justify-between gap-2">
		<h3 class="text-xs font-medium text-gray-700 dark:text-gray-300">
			{$t('quotaSettings.title')}
		</h3>
		<button
			type="button"
			class="flex items-center gap-1 text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
			onclick={() => refreshQuota(true)}
		>
			<span class="flex" class:animate-spin={$quotaLoading}><Icon name="refresh" size={11} /></span>
			{$t('usageIndicator.refresh')}
		</button>
	</div>

	<div class="mt-2 flex items-start gap-2 text-xs">
		<span class="mt-0.5 shrink-0 font-medium text-gray-700 dark:text-gray-300">SuperGrok</span>
		<span class="min-w-0 flex-1 text-gray-500">
			{#if isSuperGrokQuota(supergrok)}
				{$t('usageIndicator.remaining', { percent: Math.round(supergrok.remaining_percent) })}
				{#if supergrok.resets_at}
					· {new Date(supergrok.resets_at * 1000).toLocaleString()}
				{/if}
			{:else if supergrok && 'error' in supergrok}
				<span class="text-amber-600 dark:text-amber-400">{$t('usageIndicator.supergrokError')}</span
				>
			{:else}
				{$t('quotaSettings.supergrokMissing')}
			{/if}
		</span>
	</div>

	<div class="app-divider my-3 h-px"></div>

	<div class="flex flex-wrap items-center gap-2">
		<span class="text-xs text-gray-600 dark:text-gray-400">{$t('quotaSettings.budget')}</span>
		<div class="ml-auto flex items-center gap-0.5 rounded-lg p-0.5 period-group">
			{#each periods as period}
				<button
					type="button"
					class="h-6 rounded-md px-2 text-[0.6875rem] transition-colors"
					class:period-active={$quotaBudget.period === period}
					onclick={() => setPeriod(period)}
				>
					{$t(`usageIndicator.period.${period}`)}
				</button>
			{/each}
		</div>
	</div>
	<form
		class="mt-2 flex items-center gap-1.5"
		onsubmit={(event) => {
			event.preventDefault();
			saveBudget();
		}}
	>
		<input
			bind:value={budgetDraft}
			placeholder={$t('quotaSettings.budgetPlaceholder')}
			aria-label={$t('quotaSettings.budget')}
			class="app-subtle-surface h-7 min-w-0 flex-1 rounded-lg border px-2 text-xs outline-none"
		/>
		<button
			type="submit"
			class="h-7 shrink-0 rounded-lg px-3 text-xs font-medium save-btn"
			disabled={budgetDraft.trim() !== '' && !parseTokens(budgetDraft)}
		>
			{$t('modelHub.contextSave')}
		</button>
		{#if $quotaBudget.tokens}
			<button
				type="button"
				class="h-7 shrink-0 rounded-lg px-2 text-xs text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
				onclick={clearBudget}
			>
				{$t('quotaSettings.clear')}
			</button>
		{/if}
	</form>
	<p class="mt-1.5 text-[0.6875rem] leading-snug text-gray-400 dark:text-gray-500">
		{#if $quotaSummary.source === 'budget'}
			{$t('usageIndicator.budgetTokens', {
				used: formatTokens($quotaSummary.tokensUsed),
				budget: formatTokens($quotaSummary.budget)
			})}
			· {$t('usageIndicator.remaining', { percent: Math.round($quotaSummary.remainingPercent) })}
		{:else}
			{$t('quotaSettings.budgetHint')}
		{/if}
	</p>
</section>

<style>
	.quota-card {
		padding: 0.75rem;
		border-radius: 0.75rem;
		border: 1px solid color-mix(in oklab, var(--app-fg) 10%, transparent);
	}

	.period-group {
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
	}

	.period-group button {
		color: var(--app-fg-muted);
	}

	.period-group button.period-active {
		background: var(--app-bg);
		color: var(--app-fg);
		font-weight: 600;
	}

	.save-btn {
		background: var(--app-accent);
		color: var(--app-accent-fg);
	}

	.save-btn:disabled {
		opacity: 0.4;
	}
</style>
