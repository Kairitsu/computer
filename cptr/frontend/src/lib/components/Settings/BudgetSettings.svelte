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
	<div class="segmented segmented-sm ml-auto" role="group" aria-label={$t('quotaSettings.budget')}>
		{#each periods as period}
			<button
				type="button"
				class:active={$quotaBudget.period === period}
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
		class="settings-field min-w-0 flex-1"
	/>
	<button
		type="submit"
		class="settings-action shrink-0"
		disabled={budgetDraft.trim() !== '' && !parseTokens(budgetDraft)}
	>
		{$t('modelHub.contextSave')}
	</button>
	{#if $quotaBudget.tokens}
		<button type="button" class="settings-action shrink-0" onclick={clearBudget}>
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
