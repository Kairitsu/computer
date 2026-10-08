import { fetchJSON } from '$lib/apis';

export type UpdateReason =
	| 'not_git'
	| 'git_missing'
	| 'no_upstream'
	| 'fetch_failed'
	| 'git_failed'
	| 'timeout'
	| 'dirty'
	| 'diverged';

export interface UpdateRevision {
	version: string;
	commit: string | null;
	date: string | null;
}

export interface ChangelogEntry {
	title: string;
	content: string;
	raw: string;
}

export type ChangelogVersion = { date: string } & Record<string, string | ChangelogEntry[]>;

export interface UpdateCheck {
	/** The install runs from a git checkout, so it can update itself. */
	supported: boolean;
	/** The upstream branch has commits this install doesn't. */
	available: boolean;
	/** Why updating isn't possible right now, if it isn't. */
	reason: UpdateReason | null;
	detail: string;
	current: UpdateRevision;
	latest: UpdateRevision | null;
	behind: number;
	commits: { commit: string; subject: string; date: string }[];
	/** CHANGELOG.md entries newer than the running version, from the upstream branch. */
	notes: Record<string, ChangelogVersion>;
	dirty_files: string[];
	branch: string | null;
	repo_url: string | null;
	can_restart: boolean;
	active_chats: number;
	checked_at: number;
}

export type UpdateStepKey =
	| 'fetch'
	| 'pull'
	| 'frontend_deps'
	| 'frontend_build'
	| 'backend_deps'
	| 'restart';

export interface UpdateStatus {
	status: 'idle' | 'running' | 'restarting' | 'done' | 'failed';
	step?: UpdateStepKey;
	steps?: { key: UpdateStepKey; status: 'pending' | 'running' | 'done' | 'skipped' | 'failed' }[];
	error?: string | null;
	detail?: string;
	rolled_back?: boolean;
	rollback_failed?: boolean;
	to_version?: string | null;
	to_commit?: string | null;
	restart_required?: boolean;
	log: string[];
}

export const checkForUpdate = (refresh = false) =>
	fetchJSON<UpdateCheck>(`/api/update${refresh ? '?refresh=true' : ''}`);

export const startUpdate = () => fetchJSON<UpdateStatus>('/api/update', { method: 'POST' });

export const getUpdateStatus = () => fetchJSON<UpdateStatus>('/api/update/status');

/** When the server process started; it changes once an update has restarted it. */
export async function getServerStartedAt(): Promise<number | null> {
	try {
		const res = await fetch('/api/health', { cache: 'no-store' });
		if (!res.ok) return null;
		return ((await res.json()) as { started_at?: number }).started_at ?? null;
	} catch {
		return null;
	}
}
