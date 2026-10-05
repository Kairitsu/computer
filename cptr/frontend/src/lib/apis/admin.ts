/**
 * Admin API: user management, instance config.
 */
import { fetchJSON, jsonBody } from '$lib/apis';

export interface AdminUser {
	user_id: string;
	username: string;
	display_name: string | null;
	profile_image_url: string | null;
	role: string;
	created_at: number;
}

export const listUsers = async (): Promise<AdminUser[]> => {
	const data = await fetchJSON<{ users: AdminUser[] }>('/api/admin/users');
	return data.users;
};

export const createUser = (username: string, password: string, role = 'user') =>
	fetchJSON('/api/admin/users', jsonBody({ username, password, role }));

export const deleteUser = (userId: string) =>
	fetchJSON(`/api/admin/users/${userId}`, { method: 'DELETE' });

export const updateRole = (userId: string, role: string) =>
	fetchJSON(`/api/admin/users/${userId}/role`, {
		...jsonBody({ role }),
		method: 'PUT'
	});

export const updateUserProfile = (userId: string, display_name: string | null) =>
	fetchJSON(`/api/admin/users/${userId}/profile`, {
		...jsonBody({ display_name }),
		method: 'PUT'
	});

export const resetPassword = (userId: string, password: string) =>
	fetchJSON(`/api/admin/users/${userId}/password`, {
		...jsonBody({ password }),
		method: 'PUT'
	});

export const updateUsername = (userId: string, username: string) =>
	fetchJSON(`/api/admin/users/${userId}/username`, {
		...jsonBody({ username }),
		method: 'PUT'
	});

export const getAdminConfig = async (): Promise<Record<string, unknown>> => {
	const data = await fetchJSON<{ config: Record<string, unknown> }>('/api/admin/config');
	return data.config;
};

export const updateConfig = (config: Record<string, unknown>) =>
	fetchJSON('/api/admin/config', {
		...jsonBody({ config }),
		method: 'PUT'
	});

// ── Agents ─────────────────────────────────────────────────

export type AgentType =
	| 'codex'
	| 'claude_code'
	| 'cursor'
	| 'grok'
	| 'opencode'
	| 'cline'
	| 'gemini'
	| 'pi';
export type AgentMode = 'auto' | 'enabled' | 'disabled';
export type AgentStatus = 'ready' | 'not_found' | 'missing_dependency' | 'auth_unknown' | 'error';

export interface AgentProfile {
	id: string;
	agent: AgentType;
	name: string;
	mode: AgentMode;
	command: string;
	home: string | null;
	models: string[];
	default_model: string;
	approval_mode?: 'ask' | 'auto' | 'full';
	sandbox_mode?: 'read-only' | 'workspace-write' | 'danger-full-access';
	permission_mode?: 'default' | 'accept_edits' | 'bypass_permissions';
	launch_args?: string;
	api_endpoint?: string;
	server_url?: string;
	server_password?: string;
}

export interface AgentsResponseProfile {
	id: string;
	agent: AgentType;
	name: string;
	config: AgentProfile;
	detected: {
		status: AgentStatus;
		command: string | null;
		version: string | null;
		message: string | null;
		models?: string[] | null;
	};
	available: boolean;
	implicit: boolean;
	model_ids: string[];
}

export interface AgentsResponse {
	profiles: AgentsResponseProfile[];
}

export const getAgents = async (): Promise<AgentsResponse> =>
	fetchJSON<AgentsResponse>('/api/admin/agents');

export const updateAgents = (profiles: AgentProfile[]): Promise<AgentsResponse> =>
	fetchJSON<AgentsResponse>('/api/admin/agents', {
		...jsonBody({ profiles }),
		method: 'PUT'
	});

export const refreshAgents = async (): Promise<AgentsResponse> =>
	fetchJSON<AgentsResponse>('/api/admin/agents/refresh', { method: 'POST' });

// ── Model Config ────────────────────────────────────────────

export interface ModelConfigEntry {
	is_active?: boolean;
	params?: {
		system_prompt?: string;
	};
}

export interface ModelConfigResponse {
	config: Record<string, ModelConfigEntry>;
	models: {
		id: string;
		name: string;
		provider: string;
		agent_id?: string;
		profile_id?: string;
	}[];
}

export const getModelConfig = async (): Promise<ModelConfigResponse> =>
	fetchJSON<ModelConfigResponse>('/api/admin/models/config');

export const refreshModelList = async (): Promise<ModelConfigResponse> =>
	fetchJSON<ModelConfigResponse>('/api/admin/models/refresh', { method: 'POST' });

export const updateModelConfig = (
	modelId: string,
	update: { is_active?: boolean; params?: Record<string, unknown> }
) =>
	fetchJSON(`/api/admin/models/${encodeURIComponent(modelId)}/config`, {
		...jsonBody(update),
		method: 'PUT'
	});
