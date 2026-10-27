import { fetchJSON, jsonBody } from '$lib/apis';
import type { CodeBuddyQuota } from '$lib/apis/chat';

/** CodeBuddy's login (without its token) and this user's CodeBuddy usage. */
export const getCodeBuddyAccount = () => fetchJSON<CodeBuddyQuota>('/api/codebuddy/account');

/** Idle CodeBuddy processes chats keep between turns (Settings → General). */
export interface CodeBuddyProcessSettings {
	max_idle_processes: number;
	idle_timeout_minutes: number;
}

export const getCodeBuddyProcessSettings = () =>
	fetchJSON<CodeBuddyProcessSettings>('/api/codebuddy/processes');

export const updateCodeBuddyProcessSettings = (settings: Partial<CodeBuddyProcessSettings>) =>
	fetchJSON<CodeBuddyProcessSettings>('/api/codebuddy/processes', {
		...jsonBody(settings),
		method: 'PUT'
	});
