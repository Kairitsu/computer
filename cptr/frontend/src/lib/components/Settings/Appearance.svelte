<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Icon from '../Icon.svelte';
	import { t } from '$lib/i18n';
	import {
		borderContrast,
		expandToolDetails,
		textScale,
		theme,
		themeConfig,
		widescreenMode
	} from '$lib/stores';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import type { Theme, ThemeConfig } from '$lib/stores';
	import {
		DEFAULT_BORDER_CONTRAST,
		MAX_BORDER_CONTRAST,
		normalizeBorderContrast,
		normalizeHexColor,
		resolveThemeMode,
		resolveThemeConfig,
		sanitizeThemeConfig,
		type ThemeColors
	} from '$lib/utils/appearance';
	import { THEME_PRESETS, type ThemePreset } from '$lib/utils/themePresets';

	// Font size is stored as a root text scale relative to the 16px default.
	const BASE_FONT_SIZE = 16;
	const MIN_FONT_SIZE = 12;
	const MAX_FONT_SIZE = 24;
	const minTextScale = MIN_FONT_SIZE / BASE_FONT_SIZE;
	const maxTextScale = MAX_FONT_SIZE / BASE_FONT_SIZE;
	const borderContrastStep = 0.5;
	const fontSizePresets = [
		{ size: 14, key: 'appearance.fontSizeSmall' },
		{ size: 16, key: 'appearance.fontSizeDefault' },
		{ size: 18, key: 'appearance.fontSizeLarge' },
		{ size: 20, key: 'appearance.fontSizeXLarge' }
	];
	type ColorKey = 'background' | 'foreground' | 'accent';

	let fileInput: HTMLInputElement;
	let section = $state<'theme' | 'interface'>('theme');
	let fontSizeDraft = $state(BASE_FONT_SIZE);
	let borderContrastEnabled = $state(false);
	let borderContrastDraft = $state(DEFAULT_BORDER_CONTRAST);
	let colorDrafts = $state<Record<ColorKey, string>>({
		background: '',
		foreground: '',
		accent: ''
	});

	const resolvedTheme = $derived(resolveThemeMode($theme));
	const resolvedConfig = $derived(resolveThemeConfig($theme, $themeConfig));
	const hasCustomAppearance = $derived(
		Boolean($themeConfig || $textScale !== null || $borderContrast !== null || $widescreenMode)
	);
	const activePresetId = $derived(
		$themeConfig?.preset ?? (!$themeConfig?.light && !$themeConfig?.dark ? 'default' : null)
	);

	$effect(() => {
		colorDrafts = {
			background: resolvedConfig.background,
			foreground: resolvedConfig.foreground,
			accent: resolvedConfig.accent
		};
		fontSizeDraft = Math.round(BASE_FONT_SIZE * ($textScale ?? 1));
		if ($borderContrast !== null) {
			borderContrastEnabled = true;
			borderContrastDraft = $borderContrast;
		} else if (!borderContrastEnabled) {
			borderContrastDraft = DEFAULT_BORDER_CONTRAST;
		}
	});

	function setTheme(v: Theme) {
		theme.set(v);
	}

	function updateThemeColors(next: ThemeColors) {
		// Hand-edited colors are no longer the preset they started from.
		const { preset: _preset, ...current } = $themeConfig ?? {};
		themeConfig.set(
			sanitizeThemeConfig({
				...current,
				[resolvedTheme]: { ...(current[resolvedTheme] ?? {}), ...next }
			})
		);
	}

	function applyPreset(preset: ThemePreset) {
		const uiFont = $themeConfig?.uiFont;
		themeConfig.set(
			sanitizeThemeConfig(
				preset.id === 'default'
					? { uiFont }
					: { light: { ...preset.light }, dark: { ...preset.dark }, uiFont, preset: preset.id }
			)
		);
	}

	function updateThemeConfig(next: ThemeConfig) {
		themeConfig.set(sanitizeThemeConfig({ ...($themeConfig ?? {}), ...next }));
	}

	function updateColor(key: ColorKey, value: string) {
		colorDrafts = { ...colorDrafts, [key]: value };
		const color = normalizeHexColor(value);
		if (color) updateThemeColors({ [key]: color });
	}

	function updateFont(value: string) {
		updateThemeConfig({ uiFont: value });
	}

	function toggleBorderContrast() {
		if (borderContrastEnabled) {
			borderContrastEnabled = false;
			borderContrastDraft = DEFAULT_BORDER_CONTRAST;
			borderContrast.set(null);
		} else {
			borderContrastEnabled = true;
			borderContrastDraft = $borderContrast ?? DEFAULT_BORDER_CONTRAST;
		}
	}

	function normalizeTextScale(scale: number | string) {
		const value = Number(scale);
		if (!Number.isFinite(value)) return minTextScale;
		return Math.max(minTextScale, Math.min(maxTextScale, Number(value.toFixed(4))));
	}

	function setFontSize(size: number | string) {
		const next = Math.max(MIN_FONT_SIZE, Math.min(MAX_FONT_SIZE, Math.round(Number(size))));
		if (!Number.isFinite(next)) return;
		fontSizeDraft = next;
		textScale.set(next === BASE_FONT_SIZE ? null : next / BASE_FONT_SIZE);
	}

	function borderContrastLabel(contrast: number | null) {
		if (contrast === null) return $t('general.default');
		return `${contrast.toFixed(contrast % 1 === 0 ? 0 : 1)}%`;
	}

	function setBorderContrastPreference(contrast: number | string) {
		const next = normalizeBorderContrast(contrast) ?? DEFAULT_BORDER_CONTRAST;
		borderContrastDraft = next;
		if (next === DEFAULT_BORDER_CONTRAST) {
			borderContrastEnabled = false;
			borderContrast.set(null);
		} else {
			borderContrastEnabled = true;
			borderContrast.set(next);
		}
	}

	function resetAppearance() {
		themeConfig.set(null);
		fontSizeDraft = BASE_FONT_SIZE;
		textScale.set(null);
		borderContrastEnabled = false;
		borderContrastDraft = DEFAULT_BORDER_CONTRAST;
		borderContrast.set(null);
		widescreenMode.set(false);
	}

	function exportTheme() {
		const payload = {
			theme: $theme,
			themeConfig: sanitizeThemeConfig($themeConfig),
			textScale: $textScale,
			borderContrast: $borderContrast,
			widescreenMode: $widescreenMode
		};

		try {
			const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = url;
			link.download = 'computer-theme.json';
			link.click();
			URL.revokeObjectURL(url);
			toast.success($t('appearance.exported'));
		} catch {
			toast.error($t('appearance.exportFailed'));
		}
	}

	function validateImportedColors(source: Record<string, unknown>) {
		for (const bucket of [source, source.light, source.dark]) {
			if (!bucket || typeof bucket !== 'object') continue;
			for (const key of ['background', 'foreground', 'accent', 'sidebar']) {
				if (key in bucket && !normalizeHexColor((bucket as Record<string, unknown>)[key])) {
					throw new Error('invalid color');
				}
			}
		}
	}

	async function importTheme(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;

		try {
			const parsed = JSON.parse(await file.text());
			const source = parsed?.themeConfig ?? parsed;
			if (!source || typeof source !== 'object') throw new Error('invalid theme');
			validateImportedColors(source);
			const importedConfig = sanitizeThemeConfig(source);
			const importedTheme = ['system', 'light', 'dark'].includes(parsed?.theme)
				? (parsed.theme as Theme)
				: null;
			const importedScale =
				typeof parsed?.textScale === 'number' && Number.isFinite(parsed.textScale)
					? normalizeTextScale(parsed.textScale)
					: undefined;
			const importedWidescreenMode =
				typeof parsed?.widescreenMode === 'boolean' ? parsed.widescreenMode : undefined;
			const importedBorderContrast =
				normalizeBorderContrast(parsed?.borderContrast) ??
				(parsed?.highContrastBorders === true ? 12 : undefined);

			if (
				!importedConfig &&
				!importedTheme &&
				importedScale === undefined &&
				importedBorderContrast === undefined &&
				importedWidescreenMode === undefined
			) {
				throw new Error('empty theme');
			}
			if (importedTheme) theme.set(importedTheme);
			if (importedConfig) themeConfig.set(importedConfig);
			if (importedScale !== undefined) textScale.set(importedScale === 1 ? null : importedScale);
			if (importedBorderContrast !== undefined)
				borderContrast.set(
					importedBorderContrast === DEFAULT_BORDER_CONTRAST ? null : importedBorderContrast
				);
			if (importedWidescreenMode !== undefined) widescreenMode.set(importedWidescreenMode);
			toast.success($t('appearance.imported'));
		} catch {
			toast.error($t('appearance.importFailed'));
		}
	}
</script>

{#snippet fontSizeControl()}
	<div class="w-full">
		<div class="flex items-center gap-2">
			<span id="font-size-label" class="text-xs text-gray-600 dark:text-gray-400">
				{$t('appearance.fontSize')}
			</span>
			<span class="ml-auto text-xs tabular-nums text-gray-500" aria-live="polite">
				{fontSizeDraft}px
			</span>
		</div>
		<div class="mt-2 flex flex-wrap gap-1">
			{#each fontSizePresets as option}
				<button
					type="button"
					class="h-7 px-2.5 rounded-lg text-xs transition-colors duration-100
					{fontSizeDraft === option.size
						? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white font-medium'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					onclick={() => setFontSize(option.size)}
				>
					{$t(option.key)}
					<span class="ml-0.5 text-[0.625rem] text-gray-400">{option.size}</span>
				</button>
			{/each}
		</div>
		<div class="flex items-center gap-1.5 pt-1.5">
			<button
				type="button"
				class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
				aria-label={$t('appearance.decreaseFontSize')}
				onclick={() => setFontSize(fontSizeDraft - 1)}
			>
				<Icon name="minus" size={12} />
			</button>
			<input
				class="appearance-range flex-1 min-w-0"
				type="range"
				min={MIN_FONT_SIZE}
				max={MAX_FONT_SIZE}
				step="1"
				bind:value={fontSizeDraft}
				aria-labelledby="font-size-label"
				aria-valuemin={MIN_FONT_SIZE}
				aria-valuemax={MAX_FONT_SIZE}
				aria-valuenow={fontSizeDraft}
				aria-valuetext="{fontSizeDraft}px"
				oninput={() => setFontSize(fontSizeDraft)}
			/>
			<button
				type="button"
				class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
				aria-label={$t('appearance.increaseFontSize')}
				onclick={() => setFontSize(fontSizeDraft + 1)}
			>
				<Icon name="plus" size={12} />
			</button>
		</div>
		<p class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600">
			{$t('appearance.fontSizeHint')}
		</p>
	</div>
{/snippet}

<div class="flex flex-col h-full">
	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-none pr-1.5 -mr-1.5">
		<div class="flex items-center justify-between mb-4">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('appearance.title')}</h2>
			<div class="flex items-center gap-2">
				<input
					bind:this={fileInput}
					type="file"
					accept="application/json,.json"
					class="hidden"
					onchange={importTheme}
				/>
				<button
					type="button"
					class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={() => fileInput?.click()}
				>
					{$t('appearance.import')}
				</button>
				<button
					type="button"
					class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={exportTheme}
				>
					{$t('appearance.exportTheme')}
				</button>
				<button
					type="button"
					class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 disabled:opacity-30 disabled:pointer-events-none"
					disabled={!hasCustomAppearance}
					onclick={resetAppearance}
				>
					{$t('appearance.reset')}
				</button>
			</div>
		</div>

		<div class="segmented mb-5" role="tablist">
			{#each [{ id: 'theme' as const, label: $t('appearance.tabTheme') }, { id: 'interface' as const, label: $t('appearance.tabInterface') }] as tab}
				<button
					type="button"
					role="tab"
					aria-selected={section === tab.id}
					class:active={section === tab.id}
					onclick={() => (section = tab.id)}
				>
					{tab.label}
				</button>
			{/each}
		</div>

		{#if section === 'theme'}
			<div class="flex items-center justify-between gap-3">
				<h3 class="text-xs text-gray-600 dark:text-gray-400">{$t('general.theme')}</h3>
				<div class="segmented segmented-sm">
					{#each [{ value: 'system' as Theme, label: $t('general.system'), icon: 'monitor' }, { value: 'light' as Theme, label: $t('general.light'), icon: 'sun-light' }, { value: 'dark' as Theme, label: $t('general.dark'), icon: 'half-moon' }] as opt}
						<button
							type="button"
							class:active={$theme === opt.value}
							onclick={() => setTheme(opt.value)}
						>
							<Icon name={opt.icon} size={12} />
							{opt.label}
						</button>
					{/each}
				</div>
			</div>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
				{$t('appearance.skins')}
			</h3>
			<div class="grid grid-cols-3 sm:grid-cols-6 gap-2">
				{#each THEME_PRESETS as preset (preset.id)}
					{@const colors = preset[resolvedTheme]}
					<button
						type="button"
						class="preset-card"
						class:selected={activePresetId === preset.id}
						style="--preset-bg: {colors.background}; --preset-fg: {colors.foreground}; --preset-accent: {colors.accent}; --preset-sidebar: {colors.sidebar};"
						aria-pressed={activePresetId === preset.id}
						onclick={() => applyPreset(preset)}
					>
						<span class="preset-preview" aria-hidden="true">
							<span class="preset-preview-sidebar"></span>
							<span class="preset-preview-main">
								<span class="preset-preview-line"></span>
								<span class="preset-preview-line short"></span>
								<span class="preset-preview-accent"></span>
							</span>
						</span>
						<span class="preset-name">
							<span class="preset-swatch" style="background: {preset.swatch};"></span>
							<span class="truncate">{$t(preset.labelKey)}</span>
						</span>
					</button>
				{/each}
			</div>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
				{$t('appearance.colors')}
				{#if !activePresetId}
					<span>· {$t('appearance.customColors')}</span>
				{/if}
			</h3>
			<div class="flex flex-col gap-2.5">
				{#each [{ key: 'background' as const, label: $t('appearance.background'), value: resolvedConfig.background }, { key: 'foreground' as const, label: $t('appearance.foreground'), value: resolvedConfig.foreground }, { key: 'accent' as const, label: $t('appearance.accent'), value: resolvedConfig.accent }] as opt}
					<label class="flex items-center justify-between gap-3">
						<span class="text-xs text-gray-600 dark:text-gray-400">{opt.label}</span>
						<div class="flex items-center gap-2 min-w-0">
							<input
								type="color"
								value={opt.value}
								class="appearance-swatch"
								aria-label={opt.label}
								oninput={(e) => updateColor(opt.key, e.currentTarget.value)}
							/>
							<input
								value={colorDrafts[opt.key]}
								class="w-24 bg-transparent text-right text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none"
								aria-label={opt.label}
								oninput={(e) => updateColor(opt.key, e.currentTarget.value)}
							/>
						</div>
					</label>
				{/each}
			</div>
		{:else}
			{@render fontSizeControl()}

			<label class="flex items-center justify-between gap-3 mt-5">
				<span class="text-xs text-gray-600 dark:text-gray-400">{$t('appearance.uiFont')}</span>
				<input
					value={$themeConfig?.uiFont ?? ''}
					placeholder={resolvedConfig.uiFont}
					class="w-full max-w-[15rem] bg-transparent text-right text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none"
					aria-label={$t('appearance.uiFont')}
					onchange={(e) => updateFont(e.currentTarget.value)}
				/>
			</label>

			<div class="w-full mt-3">
				<div class="flex items-center gap-2">
					<span id="border-contrast-label" class="text-xs text-gray-600 dark:text-gray-400">
						{$t('appearance.borderContrast')}
					</span>
					<button
						type="button"
						class="ml-auto h-6 px-2 rounded-lg text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
						aria-live="polite"
						onclick={toggleBorderContrast}
					>
						{borderContrastEnabled
							? borderContrastLabel(borderContrastDraft)
							: $t('general.default')}
					</button>
				</div>
				{#if borderContrastEnabled}
					<div class="flex items-center gap-1.5 pt-1.5">
						<button
							type="button"
							class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
							aria-labelledby="border-contrast-label"
							aria-label={$t('appearance.decreaseBorderContrast')}
							onclick={() => setBorderContrastPreference(borderContrastDraft - borderContrastStep)}
						>
							<Icon name="minus" size={12} />
						</button>
						<input
							id="border-contrast-slider"
							class="appearance-range flex-1 min-w-0"
							type="range"
							min={DEFAULT_BORDER_CONTRAST}
							max={MAX_BORDER_CONTRAST}
							step={borderContrastStep}
							bind:value={borderContrastDraft}
							aria-labelledby="border-contrast-label"
							aria-valuemin={DEFAULT_BORDER_CONTRAST}
							aria-valuemax={MAX_BORDER_CONTRAST}
							aria-valuenow={borderContrastDraft}
							aria-valuetext={borderContrastLabel(borderContrastDraft)}
							oninput={() => setBorderContrastPreference(borderContrastDraft)}
						/>
						<button
							type="button"
							class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors"
							aria-labelledby="border-contrast-label"
							aria-label={$t('appearance.increaseBorderContrast')}
							onclick={() => setBorderContrastPreference(borderContrastDraft + borderContrastStep)}
						>
							<Icon name="plus" size={12} />
						</button>
					</div>
				{/if}
			</div>

			<label class="flex items-center justify-between gap-3 mt-3">
				<span class="text-xs text-gray-600 dark:text-gray-400"
					>{$t('appearance.widescreenMode')}</span
				>
				<ToggleSwitch value={$widescreenMode} onchange={(value) => widescreenMode.set(value)} />
			</label>

			<label class="flex items-center justify-between gap-3 mt-3">
				<span class="text-xs text-gray-600 dark:text-gray-400"
					>{$t('appearance.expandToolDetails')}</span
				>
				<ToggleSwitch
					value={$expandToolDetails}
					onchange={(value) => expandToolDetails.set(value)}
				/>
			</label>
		{/if}
	</div>
</div>

<style>
	.segmented {
		display: inline-flex;
		gap: 0.125rem;
		padding: 0.1875rem;
		border-radius: 0.75rem;
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
	}

	.segmented button {
		display: inline-flex;
		align-items: center;
		gap: 0.3125rem;
		height: 1.75rem;
		padding: 0 0.875rem;
		border-radius: 0.5625rem;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
		transition:
			background 0.1s,
			color 0.1s;
	}

	.segmented-sm button {
		height: 1.5rem;
		padding: 0 0.625rem;
	}

	.segmented button:hover {
		color: var(--app-fg);
	}

	.segmented button.active {
		background: var(--app-bg);
		color: var(--app-fg);
		font-weight: 600;
		box-shadow: 0 1px 3px color-mix(in oklab, black 10%, transparent);
	}

	.preset-card {
		display: flex;
		flex-direction: column;
		gap: 0.375rem;
		min-width: 0;
		padding: 0.3125rem;
		border-radius: 0.75rem;
		border: 1px solid color-mix(in oklab, var(--app-fg) 10%, transparent);
		text-align: left;
		transition:
			border-color 0.1s,
			box-shadow 0.1s;
	}

	.preset-card:hover {
		border-color: color-mix(in oklab, var(--app-fg) 22%, transparent);
	}

	.preset-card.selected {
		border-color: var(--app-accent);
		box-shadow: 0 0 0 1px var(--app-accent);
	}

	.preset-preview {
		display: flex;
		height: 2.75rem;
		overflow: hidden;
		border-radius: 0.5rem;
		border: 1px solid color-mix(in oklab, var(--preset-fg) 10%, transparent);
		background: var(--preset-bg);
	}

	.preset-preview-sidebar {
		width: 28%;
		background: var(--preset-sidebar);
		border-right: 1px solid color-mix(in oklab, var(--preset-fg) 8%, transparent);
	}

	.preset-preview-main {
		position: relative;
		flex: 1;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		padding: 0.4375rem 0.375rem;
	}

	.preset-preview-line {
		height: 0.1875rem;
		width: 80%;
		border-radius: 999px;
		background: color-mix(in oklab, var(--preset-fg) 55%, transparent);
	}

	.preset-preview-line.short {
		width: 50%;
		background: color-mix(in oklab, var(--preset-fg) 30%, transparent);
	}

	.preset-preview-accent {
		position: absolute;
		right: 0.375rem;
		bottom: 0.375rem;
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 999px;
		background: var(--preset-accent);
	}

	.preset-name {
		display: flex;
		align-items: center;
		gap: 0.3125rem;
		min-width: 0;
		padding: 0 0.125rem 0.0625rem;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--app-fg);
	}

	.preset-swatch {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 999px;
		flex-shrink: 0;
	}

	.appearance-swatch {
		width: 1.5rem;
		height: 1.5rem;
		border: 1px solid var(--app-border);
		border-radius: 624.9375rem;
		background: transparent;
		padding: 0.125rem;
	}

	.appearance-range {
		appearance: none;
		height: 1rem;
		background: transparent;
		cursor: pointer;
	}

	.appearance-range::-webkit-slider-runnable-track {
		height: 0.1875rem;
		border-radius: 624.9375rem;
		background: color-mix(in oklab, var(--app-fg) 24%, transparent);
	}

	.appearance-range::-webkit-slider-thumb {
		appearance: none;
		width: 0.75rem;
		height: 0.75rem;
		margin-top: -0.3125rem;
		border-radius: 624.9375rem;
		border: 1px solid color-mix(in oklab, var(--app-bg) 70%, transparent);
		background: var(--app-accent);
	}

	.appearance-range::-moz-range-track {
		height: 0.1875rem;
		border-radius: 624.9375rem;
		background: color-mix(in oklab, var(--app-fg) 24%, transparent);
	}

	.appearance-range::-moz-range-thumb {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 624.9375rem;
		border: 1px solid color-mix(in oklab, var(--app-bg) 70%, transparent);
		background: var(--app-accent);
	}
</style>
