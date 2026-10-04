import type { ThemeColors } from '$lib/utils/appearance';

/**
 * Designed color skins, adapted from Grok App's skin presets (default / rose /
 * gothic / mist / ocean / ember). Each preset carries a light and a dark palette;
 * the active one follows the light/dark/system theme mode. All other UI greys are
 * derived from background + foreground, so these four colors restyle the app.
 */
export type ThemePresetId = 'default' | 'rose' | 'gothic' | 'mist' | 'ocean' | 'ember';

export type ThemePreset = {
	id: ThemePresetId;
	/** i18n key for the preset name. */
	labelKey: string;
	/** Settings card swatch (accent sample). */
	swatch: string;
	light: Required<ThemeColors>;
	dark: Required<ThemeColors>;
};

export const THEME_PRESETS: ThemePreset[] = [
	{
		id: 'default',
		labelKey: 'appearance.preset.default',
		swatch: '#8aa4ff',
		light: { background: '#ffffff', foreground: '#525252', accent: '#0a0a0a', sidebar: '#ffffff' },
		dark: { background: '#0a0a0a', foreground: '#d4d4d4', accent: '#fafafa', sidebar: '#0a0a0a' }
	},
	{
		// Salon blush: carmine on dusty mauve.
		id: 'rose',
		labelKey: 'appearance.preset.rose',
		swatch: '#d4536a',
		light: { background: '#fffbfc', foreground: '#4a2a33', accent: '#c43d55', sidebar: '#f6eef1' },
		dark: { background: '#1a1216', foreground: '#ead5db', accent: '#e06b7c', sidebar: '#161014' }
	},
	{
		// Cathedral brass on oxblood; parchment by day.
		id: 'gothic',
		labelKey: 'appearance.preset.gothic',
		swatch: '#c4a35a',
		light: { background: '#f7f1e4', foreground: '#4a3b22', accent: '#8b6914', sidebar: '#ebe2d2' },
		dark: { background: '#12100d', foreground: '#e3d6bd', accent: '#d4b56a', sidebar: '#15120f' }
	},
	{
		// Nordic fog: cool sage and slate.
		id: 'mist',
		labelKey: 'appearance.preset.mist',
		swatch: '#6f8f8a',
		light: { background: '#f5f8f8', foreground: '#34504c', accent: '#3f6b66', sidebar: '#e8eef0' },
		dark: { background: '#14191c', foreground: '#cfdcdc', accent: '#7a9e98', sidebar: '#12171a' }
	},
	{
		// Deep harbor: teal-cyan.
		id: 'ocean',
		labelKey: 'appearance.preset.ocean',
		swatch: '#2eb8c7',
		light: { background: '#f4fafc', foreground: '#1e3a4a', accent: '#0d7c8c', sidebar: '#e4eef4' },
		dark: { background: '#0c1520', foreground: '#d2e6ee', accent: '#3ec8d6', sidebar: '#0a121c' }
	},
	{
		// Forge coal: copper flame.
		id: 'ember',
		labelKey: 'appearance.preset.ember',
		swatch: '#e8893a',
		light: { background: '#fbf6f0', foreground: '#4a3020', accent: '#c45a12', sidebar: '#f3ebe2' },
		dark: { background: '#16100c', foreground: '#ecd9c8', accent: '#f08a3c', sidebar: '#130e0a' }
	}
];

export const THEME_PRESET_IDS = new Set<string>(THEME_PRESETS.map((preset) => preset.id));

export function getThemePreset(id: unknown): ThemePreset | undefined {
	return THEME_PRESETS.find((preset) => preset.id === id);
}
