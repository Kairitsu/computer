<script lang="ts">
	/**
	 * Settings → Usage: the local token budget the composer and sidebar compare
	 * this period's usage against when no SuperGrok quota is available.
	 */
	import { quotaBudget, type QuotaPeriod } from '$lib/stores';
	import { formatTokens, quotaSummary } from '$lib/stores/quota';
	import { t } from '$lib/i18n';

	const periods: QuotaPeriod[] = ['day', 'week', 'month'];

	let budgetDraft = $state('');

	$effect(() => {
		budgetDraft = $quotaBudget.tokens ? String($quotaBudget.tokens) : '';
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

<div class="flex flex-wrap items-center gap-2">
	<span class="text-[0.8125rem] font-semibold" style="color: var(--app-fg);"
		>{$t('quotaSettings.budget')}</span
	>
	<div class="period-group ml-auto flex items-center gap-0.5 rounded-lg p-0.5">
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
		class="budget-input h-7 min-w-0 flex-1 rounded-lg border px-2 text-xs outline-none"
	/>
	<button
		type="submit"
		class="save-btn h-7 shrink-0 rounded-lg px-3 text-xs font-medium"
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
<p class="mt-1.5 text-[0.6875rem] leading-snug" style="color: var(--app-fg-subtle);">
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

<style>
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

	.budget-input {
		background: var(--app-bg);
		color: var(--app-fg);
		border-color: color-mix(in oklab, var(--app-fg) 12%, transparent);
	}

	.save-btn {
		background: var(--app-accent);
		color: var(--app-accent-fg);
	}

	.save-btn:disabled {
		opacity: 0.4;
	}
</style>
