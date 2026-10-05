/**
 * Admin API: instance config, agents and models.
 */
import { fetchJSON, jsonBody } from '$lib/apis';

export const getAdminConfig = async (): Promise<Record<string, unknown>> => {
	const data = await fetchJSON<{ config: Record<string, unknown> }>('/api/admin/config');
	return data.config;
};

export const updateConfig = (config: Record<string, unknown>) =>
	fetchJSON('/api/admin/config', {
		...jsonBody({ config }),
		method: 'PUT'
	});

// ── Chat history retention ──────────────────────────────────

/** Days without activity after which chats are deleted; 0 keeps them forever. */
export interface ChatRetention {
	days: number;
	max_days: number;
}

export const getChatRetention = () => fetchJSON<ChatRetention>('/api/admin/chat-retention');

export const previewChatRetention = (days: number) =>
	fetchJSON<{ count: number }>(`/api/admin/chat-retention/preview?days=${days}`);

export const updateChatRetention = (days: number) =>
	fetchJSON<ChatRetention>('/api/admin/chat-retention', {
		...jsonBody({ days }),
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
