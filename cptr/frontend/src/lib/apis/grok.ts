import { fetchJSON, jsonBody } from '$lib/apis';
import type { QuotaResponse } from '$lib/apis/chat';

export interface GrokProfile {
	signed_in: boolean;
	email: string | null;
	display_name: string | null;
	user_id: string | null;
	team_id: string | null;
	expires_at: string | null;
	expired: boolean;
	has_refresh: boolean;
}

export interface GrokSavedAccount {
	id: string;
	label: string;
	email: string | null;
	saved_at: number | null;
	active: boolean;
}

export type GrokLoginState = 'idle' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'expired';

export interface GrokLoginStatus {
	state: GrokLoginState;
	method?: 'oauth' | 'device';
	/** Sign-in page printed by `grok login`. */
	url?: string;
	/** Device code to enter on that page. */
	code?: string;
	message?: string;
	started_at?: number;
}

export interface GrokAccount {
	profile: GrokProfile;
	cli_found: boolean;
	accounts: GrokSavedAccount[];
	login: GrokLoginStatus;
	supergrok: QuotaResponse['supergrok'];
	/** Signing in/out and switching need admin. */
	can_manage: boolean;
}

export const getGrokAccount = (refresh = false) =>
	fetchJSON<GrokAccount>(`/api/grok/account${refresh ? '?refresh=true' : ''}`);

export const startGrokLogin = (method: 'oauth' | 'device') =>
	fetchJSON<GrokLoginStatus>('/api/grok/login', jsonBody({ method }));

export const getGrokLogin = () => fetchJSON<GrokLoginStatus>('/api/grok/login');

export const submitGrokLoginCode = (code: string) =>
	fetchJSON<GrokLoginStatus>('/api/grok/login/code', jsonBody({ code }));

export const cancelGrokLogin = () =>
	fetchJSON<GrokLoginStatus>('/api/grok/login/cancel', { method: 'POST' });

export const logoutGrok = () => fetchJSON<{ ok: boolean }>('/api/grok/logout', { method: 'POST' });

export const saveGrokAccount = () =>
	fetchJSON<{ id: string }>('/api/grok/accounts', { method: 'POST' });

export const switchGrokAccount = (id: string) =>
	fetchJSON<{ profile: GrokProfile }>(`/api/grok/accounts/${encodeURIComponent(id)}/switch`, {
		method: 'POST'
	});

export const removeGrokAccount = (id: string) =>
	fetchJSON<{ ok: boolean }>(`/api/grok/accounts/${encodeURIComponent(id)}`, {
		method: 'DELETE'
	});

/** Idle Grok processes chats keep between turns (Settings → General). */
export interface GrokProcessSettings {
	max_idle_processes: number;
	idle_timeout_minutes: number;
}

export const getGrokProcessSettings = () => fetchJSON<GrokProcessSettings>('/api/grok/processes');

export const updateGrokProcessSettings = (settings: Partial<GrokProcessSettings>) =>
	fetchJSON<GrokProcessSettings>('/api/grok/processes', {
		...jsonBody(settings),
		method: 'PUT'
	});
