/**
 * State API: user preferences, workspace state, welcome/system info.
 *
 * State is split into three layers:
 *   - preferences: global user prefs (theme, locale, etc.)
 *   - workspaces: per-workspace state keyed by filesystem path
 *   - active workspace: determined by URL query param, not stored server-side
 */
import { fetchHandler, fetchJSON, jsonBody } from '$lib/apis';

// ── Preferences ─────────────────────────────────────────────────

export const getPreferences = () => fetchJSON<Record<string, unknown>>('/api/state/preferences');

export const savePreferences = (data: Record<string, unknown>) =>
	fetchHandler('/api/state/preferences', { ...jsonBody(data), method: 'PUT' });

// ── Workspace list (sidebar) ────────────────────────────────────

export interface WorkspaceListItem {
	path: string;
	name: string;
	unread_count: number;
}

export const getWorkspaceList = () => fetchJSON<WorkspaceListItem[]>('/api/state/workspaces');

// ── Single workspace CRUD ───────────────────────────────────────

export const getWorkspaceState = (path: string) =>
	fetchJSON<Record<string, unknown>>(`/api/state/workspace?path=${encodeURIComponent(path)}`);

export const saveWorkspaceState = (path: string, data: Record<string, unknown>) =>
	fetchHandler(`/api/state/workspace?path=${encodeURIComponent(path)}`, {
		...jsonBody(data),
		method: 'PUT'
	});

export const deleteWorkspace = (path: string) =>
	fetchHandler(`/api/state/workspace?path=${encodeURIComponent(path)}`, { method: 'DELETE' });

// ── Welcome page ────────────────────────────────────────────────

export const getWelcome = () => fetchJSON<Record<string, unknown>>('/api/state/welcome');

// ── System info ─────────────────────────────────────────────────

/** This host's IPv4 address and the country it geolocates to. */
export interface HostNetwork {
	ipv4: string | null;
	/** ISO 3166-1 alpha-2 code, e.g. "US"; null when the lookup failed. */
	region: string | null;
	/** False when only a private address could be found. */
	public: boolean;
}

export const getHostNetwork = () => fetchJSON<HostNetwork>('/api/state/network');
