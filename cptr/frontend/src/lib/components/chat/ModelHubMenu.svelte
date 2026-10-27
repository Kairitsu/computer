<script lang="ts">
	/**
	 * Composer model hub: one chip for model + reasoning effort + context window.
	 * When the model's agent CLI lists its efforts / context windows (Grok, CodeBuddy),
	 * the hub offers exactly those, and effort names are the CLI values themselves.
	 * Switching models selects the strongest effort and the longest window.
	 * After the chat has a user message, the chip only displays the current choice.
	 */
	import { tick } from 'svelte';
	import type { ReasoningEffort } from '$lib/apis/chat';
	import { highestEffort, longestContext } from '$lib/modelCaps';
	import { chatModels, profileOfModel } from '$lib/stores/chat';
	import { formatTokens } from '$lib/stores/quota';
	import { t } from '$lib/i18n';
	import Icon from '../Icon.svelte';
	import Popover from '../common/Popover.svelte';

	interface Props {
		selectedModel: string;
		reasoningEffort?: ReasoningEffort | null;
		contextWindow?: number | null;
		onchange?: () => void;
		onclose?: () => void;
		/** Save the current agent's settings before a different agent's model is shown. */
		onbeforeprofilechange?: (profile: string, modelId: string) => void;
		/** A sent chat shows the current model, effort, and context, and cannot change them. */
		locked?: boolean;
	}

	let {
		selectedModel = $bindable(),
		reasoningEffort = $bindable(null),
		contextWindow = $bindable(null),
		onchange,
		onclose,
		onbeforeprofilechange,
		locked = false
	}: Props = $props();

	type Page = 'main' | 'models' | 'effort' | 'context';

	const FALLBACK_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high', 'xhigh'];
	const EFFORT_LEVELS: Record<string, number> = {
		minimal: 0,
		low: 1,
		medium: 2,
		high: 3,
		xhigh: 4,
		max: 5
	};
	const CONTEXT_WINDOWS = [32_000, 64_000, 128_000, 200_000, 256_000, 500_000, 1_000_000];

	let btnEl: HTMLButtonElement | undefined = $state();
	let searchInputEl: HTMLInputElement | undefined = $state();
	let open = $state(false);
	let page = $state<Page>('main');
	let search = $state('');
	let highlightedIndex = $state(0);
	let customContext = $state('');

	const model = $derived($chatModels.find((m) => m.id === selectedModel));
	const supportsEffort = $derived(model?.supports_reasoning_effort === true);
	const cliEfforts = $derived(model?.reasoning_efforts?.length ? model.reasoning_efforts : null);
	const cliContextWindows = $derived(model?.context_windows?.length ? model.context_windows : null);
	// A choice the model does not offer shows as its strongest effort and longest window.
	const effectiveEffort = $derived(
		cliEfforts && !cliEfforts.some((option) => option.value === reasoningEffort)
			? highestEffort(model)
			: reasoningEffort
	);
	const effectiveContextWindow = $derived(
		cliContextWindows && !(contextWindow && cliContextWindows.includes(contextWindow))
			? longestContext(model)
			: (contextWindow ?? model?.context_window ?? null)
	);
	const effortLabel = $derived(effectiveEffort ?? '');
	const contextLabel = $derived(
		effectiveContextWindow ? formatTokens(effectiveContextWindow) : '—'
	);
	const filteredModels = $derived(
		search.trim()
			? $chatModels.filter((m) => m.name.toLowerCase().includes(search.trim().toLowerCase()))
			: $chatModels
	);
	const triggerLabel = $derived(
		$chatModels.length === 0
			? $t('modelSelector.noModels')
			: (model?.name ?? $t('modelSelector.selectModel'))
	);
	const lockedTitle = $derived(
		[
			triggerLabel,
			supportsEffort && effortLabel ? effortLabel : '',
			effectiveContextWindow ? contextLabel : ''
		]
			.filter(Boolean)
			.join(' · ')
	);

	export async function openSelector() {
		if (locked || $chatModels.length === 0) return;
		open = true;
		await showPage('models');
	}

	async function showPage(next: Page) {
		page = next;
		if (next === 'models') {
			search = '';
			const index = filteredModels.findIndex((m) => m.id === selectedModel);
			highlightedIndex = Math.max(0, index);
			await tick();
			searchInputEl?.focus();
		}
		if (next === 'context') {
			customContext =
				contextWindow && !CONTEXT_WINDOWS.includes(contextWindow) ? String(contextWindow) : '';
		}
	}

	function toggle() {
		if (locked) return;
		if (open) return close();
		open = true;
		page = 'main';
	}

	function close() {
		open = false;
		page = 'main';
		onclose?.();
	}

	function selectModel(id: string) {
		if (id === selectedModel) {
			page = 'main';
			return;
		}
		const next = $chatModels.find((m) => m.id === id);
		const nextProfile = profileOfModel(id);
		const sameAgent = nextProfile === profileOfModel(selectedModel);
		// Another agent's model keeps its permission mode, then takes that model's top caps.
		if (!sameAgent && nextProfile) onbeforeprofilechange?.(nextProfile, id);
		else {
			reasoningEffort = highestEffort(next);
			contextWindow = longestContext(next);
		}
		selectedModel = id;
		onchange?.();
		page = 'main';
	}

	function selectEffort(effort: ReasoningEffort | null) {
		reasoningEffort = effort;
		onchange?.();
		page = 'main';
	}

	function selectContext(value: number | null) {
		contextWindow = value;
		onchange?.();
		page = 'main';
	}

	/** "300k" / "1.5m" / "250000" → tokens. */
	function parseTokens(raw: string): number | null {
		const match = /^\s*(\d+(?:\.\d+)?)\s*([km]?)\s*$/i.exec(raw);
		if (!match) return null;
		const unit = match[2].toLowerCase();
		const value = Number(match[1]) * (unit === 'm' ? 1_000_000 : unit === 'k' ? 1_000 : 1);
		return value >= 1_000 ? Math.round(value) : null;
	}

	function saveCustomContext() {
		const value = parseTokens(customContext);
		if (value) selectContext(value);
	}

	function handleSearchKeydown(event: KeyboardEvent) {
		const total = filteredModels.length;
		if (event.key === 'ArrowDown' && total) {
			event.preventDefault();
			highlightedIndex = (highlightedIndex + 1) % total;
		} else if (event.key === 'ArrowUp' && total) {
			event.preventDefault();
			highlightedIndex = (highlightedIndex - 1 + total) % total;
		} else if (event.key === 'Enter') {
			event.preventDefault();
			const target = filteredModels[Math.min(highlightedIndex, total - 1)];
			if (target) selectModel(target.id);
		}
	}
</script>

{#if locked}
	<div class="hub-chip hub-chip-locked" title={lockedTitle}>
		<Icon name="flash" size={13} class="shrink-0" />
		<span class="truncate max-w-[11rem]">{triggerLabel}</span>
		{#if supportsEffort && effortLabel}
			<span class="shrink-0">{effortLabel}</span>
		{/if}
		{#if effectiveContextWindow}
			<span class="shrink-0 tabular-nums">{contextLabel}</span>
		{/if}
	</div>
{:else}
	<button
		bind:this={btnEl}
		type="button"
		class="hub-chip"
		aria-haspopup="menu"
		aria-expanded={open}
		title={triggerLabel}
		onclick={toggle}
	>
		<Icon name="flash" size={13} class="shrink-0" />
		<span class="truncate max-w-[11rem]">{triggerLabel}</span>
		{#if supportsEffort && effortLabel}
			<span class="shrink-0">{effortLabel}</span>
		{/if}
		{#if $chatModels.length > 0}
			<Icon name="chevron-down" size={11} class="shrink-0 opacity-60" />
		{/if}
	</button>
{/if}

{#if !locked && open && btnEl}
	<Popover anchor={btnEl} align="end" width="16rem" onclose={close}>
		{#if page === 'main'}
			<div class="hub-page">
				<button type="button" class="hub-row" onclick={() => showPage('models')}>
					<Icon name="cube" size={14} />
					<span class="hub-row-label">{$t('modelHub.model')}</span>
					<span class="hub-row-value">{model?.name ?? '—'}</span>
					<Icon name="chevron-right" size={12} class="shrink-0 opacity-60" />
				</button>
				<button
					type="button"
					class="hub-row"
					disabled={!supportsEffort}
					title={supportsEffort ? undefined : $t('modelHub.effortUnsupported')}
					onclick={() => showPage('effort')}
				>
					<Icon name="brain" size={14} />
					<span class="hub-row-label">{$t('modelHub.effort')}</span>
					<span class="hub-row-value"
						>{supportsEffort ? effortLabel || '—' : $t('modelHub.effortUnsupportedShort')}</span
					>
					<Icon name="chevron-right" size={12} class="shrink-0 opacity-60" />
				</button>
				<button type="button" class="hub-row" onclick={() => showPage('context')}>
					<Icon name="list" size={14} />
					<span class="hub-row-label">{$t('modelHub.context')}</span>
					<span class="hub-row-value">{contextLabel}</span>
					<Icon name="chevron-right" size={12} class="shrink-0 opacity-60" />
				</button>
			</div>
		{:else}
			<div class="hub-page hub-slide">
				<button type="button" class="hub-row hub-back" onclick={() => (page = 'main')}>
					<Icon name="chevron-left" size={12} />
					<span class="hub-row-label font-medium"
						>{page === 'models'
							? $t('modelHub.model')
							: page === 'effort'
								? $t('modelHub.effort')
								: $t('modelHub.context')}</span
					>
				</button>
				<div class="app-divider h-px mx-1 my-0.5"></div>

				{#if page === 'models'}
					<div class="flex items-center gap-1.5 h-7 px-2">
						<Icon name="search" size={12} class="shrink-0 opacity-50" />
						<input
							bind:this={searchInputEl}
							bind:value={search}
							placeholder={$t('modelSelector.search')}
							class="w-full bg-transparent text-xs outline-none placeholder:text-gray-400"
							oninput={() => (highlightedIndex = 0)}
							onkeydown={handleSearchKeydown}
						/>
					</div>
					<div class="max-h-60 overflow-y-auto">
						{#each filteredModels as option, index (option.id)}
							{#if index === 0 || option.profile_name !== filteredModels[index - 1]?.profile_name}
								<div class="px-2 pt-1.5 pb-0.5 text-[0.625rem] font-semibold text-gray-400">
									{option.profile_name || option.agent_id}
								</div>
							{/if}
							<button
								type="button"
								class="hub-option"
								class:highlighted={index === highlightedIndex}
								title={option.name}
								onclick={() => selectModel(option.id)}
								onmouseenter={() => (highlightedIndex = index)}
							>
								<span class="truncate flex-1 text-left">{option.name}</span>
								{#if option.credits}
									<!-- CodeBuddy's credit rate for the model -->
									<span class="shrink-0 text-[0.625rem] tabular-nums text-gray-400"
										>{option.credits}</span
									>
								{/if}
								{#if option.id === selectedModel}
									<Icon name="check" size={12} class="shrink-0 hub-check" />
								{/if}
							</button>
						{:else}
							<div class="px-2 py-1.5 text-center text-[0.6875rem] text-gray-400">
								{$t('modelSelector.noMatches')}
							</div>
						{/each}
					</div>
				{:else if page === 'effort' && cliEfforts}
					{#each cliEfforts as option (option.value)}
						<button
							type="button"
							class="hub-option"
							class:active={effectiveEffort === option.value}
							onclick={() => selectEffort(option.value)}
						>
							<span class="flex-1 text-left">{option.value}</span>
							{#if EFFORT_LEVELS[option.value]}
								<span class="effort-bars" aria-hidden="true">
									{#each [1, 2, 3, 4] as level}
										<span class:on={level <= EFFORT_LEVELS[option.value]}></span>
									{/each}
								</span>
							{/if}
							{#if effectiveEffort === option.value}
								<Icon name="check" size={12} class="shrink-0 hub-check" />
							{/if}
						</button>
					{/each}
				{:else if page === 'effort'}
					{#each FALLBACK_EFFORTS as effort}
						<button
							type="button"
							class="hub-option"
							class:active={reasoningEffort === effort}
							onclick={() => selectEffort(effort)}
						>
							<span class="flex-1 text-left">{effort}</span>
							{#if EFFORT_LEVELS[effort]}
								<span class="effort-bars" aria-hidden="true">
									{#each [1, 2, 3, 4] as level}
										<span class:on={level <= EFFORT_LEVELS[effort]}></span>
									{/each}
								</span>
							{/if}
							{#if reasoningEffort === effort}
								<Icon name="check" size={12} class="shrink-0 hub-check" />
							{/if}
						</button>
					{/each}
					<p class="px-2 pt-1 pb-0.5 text-[0.625rem] leading-snug text-gray-400">
						{$t('modelHub.effortHint')}
					</p>
				{:else if cliContextWindows}
					{#each cliContextWindows as value}
						<button
							type="button"
							class="hub-option"
							class:active={effectiveContextWindow === value}
							onclick={() => selectContext(value)}
						>
							<span class="flex-1 text-left tabular-nums">{formatTokens(value)}</span>
							{#if effectiveContextWindow === value}
								<Icon name="check" size={12} class="shrink-0 hub-check" />
							{/if}
						</button>
					{/each}
				{:else}
					{#each CONTEXT_WINDOWS as value}
						<button
							type="button"
							class="hub-option"
							class:active={effectiveContextWindow === value}
							onclick={() => selectContext(value)}
						>
							<span class="flex-1 text-left tabular-nums">{formatTokens(value)}</span>
							{#if effectiveContextWindow === value}
								<Icon name="check" size={12} class="shrink-0 hub-check" />
							{/if}
						</button>
					{/each}
					<div class="app-divider h-px mx-1 my-0.5"></div>
					<form
						class="flex items-center gap-1 px-1.5 py-1"
						onsubmit={(event) => {
							event.preventDefault();
							saveCustomContext();
						}}
					>
						<input
							bind:value={customContext}
							placeholder={$t('modelHub.contextCustomPlaceholder')}
							aria-label={$t('modelHub.contextCustom')}
							class="app-subtle-surface min-w-0 flex-1 h-6 rounded-md border px-2 text-xs outline-none"
						/>
						<button
							type="submit"
							class="h-6 shrink-0 rounded-md px-2 text-xs font-medium hub-save"
							disabled={!parseTokens(customContext)}
						>
							{$t('modelHub.contextSave')}
						</button>
					</form>
					<p class="px-2 pb-0.5 text-[0.625rem] leading-snug text-gray-400">
						{$t('modelHub.contextHint')}
					</p>
				{/if}
			</div>
		{/if}
	</Popover>
{/if}

<style>
	.hub-chip {
		display: inline-flex;
		align-items: center;
		gap: 0.3125rem;
		height: 1.875rem;
		max-width: 100%;
		padding: 0 0.75rem;
		border-radius: 0.75rem;
		border: 1px solid color-mix(in oklab, var(--app-fg) 10%, transparent);
		background: var(--app-bg);
		color: var(--app-accent);
		font-size: 0.75rem;
		font-weight: 500;
		box-shadow: 0 1px 2px color-mix(in oklab, black 5%, transparent);
		transition: border-color 0.1s;
	}

	.hub-chip:hover:not(.hub-chip-locked) {
		border-color: color-mix(in oklab, var(--app-accent) 40%, transparent);
	}

	.hub-chip-locked {
		cursor: default;
	}

	.hub-page {
		display: flex;
		flex-direction: column;
	}

	.hub-slide {
		animation: hubSlide 0.14s ease-out;
	}

	@keyframes hubSlide {
		from {
			opacity: 0;
			transform: translateX(0.5rem);
		}
		to {
			opacity: 1;
			transform: translateX(0);
		}
	}

	.hub-row,
	.hub-option {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		width: 100%;
		min-height: 1.875rem;
		padding: 0 0.5rem;
		border-radius: 0.5rem;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
		transition:
			background 0.075s,
			color 0.075s;
	}

	.hub-row:hover:not(:disabled),
	.hub-option:hover,
	.hub-option.highlighted {
		background: var(--app-hover);
		color: var(--app-fg);
	}

	.hub-row:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.hub-option.active {
		color: var(--app-fg);
		font-weight: 500;
	}

	.hub-row-label {
		flex: 1;
		text-align: left;
		color: var(--app-fg);
	}

	.hub-row-value {
		max-width: 7.5rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.6875rem;
	}

	.hub-back {
		min-height: 1.75rem;
	}

	.hub-option :global(.hub-check) {
		color: var(--app-accent);
	}

	.hub-save {
		background: var(--app-accent);
		color: var(--app-accent-fg);
	}

	.hub-save:disabled {
		opacity: 0.4;
	}

	.effort-bars {
		display: inline-flex;
		align-items: flex-end;
		gap: 0.125rem;
		height: 0.625rem;
	}

	.effort-bars span {
		width: 0.1875rem;
		border-radius: 1px;
		background: color-mix(in oklab, var(--app-fg) 18%, transparent);
	}

	.effort-bars span:nth-child(1) {
		height: 40%;
	}
	.effort-bars span:nth-child(2) {
		height: 60%;
	}
	.effort-bars span:nth-child(3) {
		height: 80%;
	}
	.effort-bars span:nth-child(4) {
		height: 100%;
	}

	.effort-bars span.on {
		background: var(--app-accent);
	}
</style>
