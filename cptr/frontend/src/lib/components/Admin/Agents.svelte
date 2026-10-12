<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getAgents,
		refreshAgents,
		updateAgents,
		type AgentProfile,
		type AgentsResponseProfile
	} from '$lib/apis/admin';
	import Icon from '$lib/components/Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import { t } from '$lib/i18n';
	import { refreshChatState } from '$lib/stores/chat';
	import AgentProfileModal from './AgentProfileModal.svelte';
	import Models from './Models.svelte';

	type ModalState =
		| { mode: 'create'; profile: AgentProfile }
		| { mode: 'edit'; index: number; profile: AgentProfile };

	let loading = $state(true);
	let saving = $state(false);
	let refreshing = $state(false);
	let profiles = $state<AgentProfile[]>([]);
	let detected = $state<Record<string, AgentsResponseProfile['detected']>>({});
	let available = $state<Record<string, boolean>>({});
	let saved = $state<Record<string, boolean>>({});
	let modal = $state<ModalState | null>(null);
	let deletedProfile = $state(false);
	let modelsApi: { refresh: () => Promise<void>; save: () => Promise<void> } | null = null;

	function registerModels(api: { refresh: () => Promise<void>; save: () => Promise<void> }) {
		modelsApi = api;
	}

	const statusLabelKey: Record<string, string> = {
		ready: 'admin.agentsStatusReady',
		not_found: 'admin.agentsStatusNotFound',
		missing_dependency: 'admin.agentsStatusMissingDependency',
		auth_unknown: 'admin.agentsStatusAuthUnknown',
		error: 'admin.agentsStatusError'
	};

	function normalizeProfile(profile: AgentProfile): AgentProfile {
		const models = profile.models?.map((m) => m.trim()).filter(Boolean);
		const nextModels = models.length ? models : [];
		const defaultModel = nextModels.includes(profile.default_model)
			? profile.default_model
			: nextModels[0] || '';
		return {
			...profile,
			id: profile.id.trim(),
			name: profile.name.trim() || profile.id.trim(),
			command: profile.command.trim() || defaultCommand(profile.agent),
			home: profile.home?.trim() || null,
			models: nextModels,
			default_model: defaultModel
		};
	}

	function applyResponse(data: { profiles: AgentsResponseProfile[] }) {
		profiles = data.profiles.map((entry) => entry.config);
		detected = Object.fromEntries(data.profiles.map((entry) => [entry.id, entry.detected]));
		available = Object.fromEntries(data.profiles.map((entry) => [entry.id, entry.available]));
		saved = Object.fromEntries(data.profiles.map((entry) => [entry.id, !entry.implicit]));
		deletedProfile = false;
	}

	function newProfile(agent: AgentProfile['agent'] = 'grok'): AgentProfile {
		const base = defaultProfileId(agent);
		let suffix = profiles.length + 1;
		let id = `${base}-${suffix}`;
		while (profiles.some((p) => p.id === id)) {
			suffix += 1;
			id = `${base}-${suffix}`;
		}
		return {
			id,
			agent,
			name: agentLabel(agent),
			mode: 'enabled',
			command: defaultCommand(agent),
			home: null,
			models: [],
			default_model: ''
		};
	}

	function profileSubtitle(profile: AgentProfile): string {
		const modelCount = profile.models?.length || 0;
		const modelLabel =
			modelCount === 0
				? 'auto-detect'
				: modelCount === 1
					? profile.default_model
					: `${modelCount} models`;
		return `agent:${profile.id} · ${modelLabel}`;
	}

	function defaultProfileId(agent: AgentProfile['agent']): string {
		return agent;
	}

	function defaultCommand(agent: AgentProfile['agent']): string {
		return { grok: 'grok' }[agent];
	}

	function agentLabel(agent: AgentProfile['agent']): string {
		return { grok: 'Grok' }[agent];
	}

	function statusKey(profile: AgentProfile): string {
		return statusLabelKey[detected[profile.id]?.status || 'error'] || statusLabelKey.error;
	}

	function statusTone(profile: AgentProfile): string {
		if (profile.mode === 'disabled') return 'bg-gray-300 dark:bg-gray-700';
		const status = detected[profile.id]?.status || 'error';
		if (available[profile.id] && status === 'ready') return 'bg-emerald-500 dark:bg-emerald-400';
		if (status === 'missing_dependency' || status === 'auth_unknown') {
			return 'bg-amber-400 dark:bg-amber-300';
		}
		return 'bg-gray-300 dark:bg-gray-700';
	}

	function saveProfile(profile: AgentProfile) {
		const normalized = normalizeProfile(profile);
		const currentModal = modal;
		if (currentModal?.mode === 'edit') {
			const previousId = currentModal.profile.id;
			profiles = profiles.map((current, index) =>
				index === currentModal.index ? normalized : current
			);
			if (previousId !== normalized.id) {
				const { [previousId]: _removed, ...rest } = saved;
				saved = rest;
			}
		} else {
			profiles = [...profiles, normalized];
		}
		saved = { ...saved, [normalized.id]: true };
		modal = null;
	}

	async function deleteProfile() {
		const currentModal = modal;
		if (currentModal?.mode !== 'edit') {
			modal = null;
			return;
		}
		const profile = profiles[currentModal.index];
		profiles = profiles.filter((_, index) => index !== currentModal.index);
		if (profile) {
			const { [profile.id]: _removed, ...rest } = saved;
			saved = rest;
			deletedProfile = true;
		}
		modal = null;
		await save();
	}

	function toggleProfile(index: number) {
		const profile = profiles[index];
		if (!profile) return;
		const mode = profile.mode === 'disabled' ? 'enabled' : 'disabled';
		profiles = profiles.map((current, i) => (i === index ? { ...current, mode } : current));
		saved = { ...saved, [profile.id]: true };
	}

	async function persistAgents() {
		const savedProfiles = deletedProfile
			? profiles
			: profiles.filter((profile) => saved[profile.id]);
		if (savedProfiles.length === 0 && !deletedProfile) return;
		applyResponse(await updateAgents(savedProfiles.map(normalizeProfile)));
	}

	async function save() {
		saving = true;
		try {
			await persistAgents();
			if (!modelsApi) throw new Error($t('models.failedToSave'));
			await modelsApi.save();
			await refreshChatState();
			toast.success($t('settings.saved'));
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $t('admin.failedToSave'));
		} finally {
			saving = false;
		}
	}

	async function refresh() {
		refreshing = true;
		let agentsOk = false;
		let modelsOk = false;
		try {
			applyResponse(await refreshAgents());
			agentsOk = true;
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		}
		try {
			if (!modelsApi) throw new Error('models unavailable');
			await modelsApi.refresh();
			modelsOk = true;
		} catch {
			toast.error($t('models.refreshFailed'));
		}
		if (agentsOk || modelsOk) {
			try {
				await refreshChatState();
			} catch {
				// The lists above already show the refreshed data.
			}
		}
		if (agentsOk && modelsOk) toast.success($t('settings.refreshed'));
		refreshing = false;
	}

	onMount(async () => {
		try {
			applyResponse(await getAgents());
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		} finally {
			loading = false;
		}
	});
</script>

<div class="flex flex-col">
	<div class="flex items-center justify-between mb-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('admin.agents')}</h2>
		<div class="flex items-center gap-1">
			<button
				type="button"
				class="settings-icon"
				onclick={refresh}
				disabled={refreshing || saving}
				title={$t('admin.agentsRefresh')}
			>
				{#if refreshing}
					<Spinner size={13} />
				{:else}
					<Icon name="refresh" size={13} />
				{/if}
			</button>
			<button
				type="button"
				class="settings-icon"
				onclick={() => (modal = { mode: 'create', profile: newProfile() })}
				title={$t('admin.agentsAddProfile')}
			>
				<Icon name="plus" size={14} />
			</button>
		</div>
	</div>

	{#if loading}
		<div class="flex justify-center py-8">
			<Spinner size={16} />
		</div>
	{:else}
		<div class="flex flex-col gap-1.5">
			{#each profiles as profile, index (profile.id)}
				<div class="settings-row flex items-center gap-2 w-full min-h-9 px-2.5 py-1.5 text-left">
					<button
						type="button"
						class="flex-1 min-w-0 text-left"
						onclick={() => (modal = { mode: 'edit', index, profile })}
					>
						<span
							class="block text-[0.8125rem] truncate {profile.mode === 'disabled'
								? 'text-gray-400 dark:text-gray-600'
								: 'text-gray-700 dark:text-gray-300'}"
						>
							{profile.name}
						</span>
						<span class="block text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
							{profileSubtitle(profile)}
						</span>
						{#if detected[profile.id]?.message}
							<span class="block text-[0.6875rem] text-gray-300 dark:text-gray-700 truncate">
								{detected[profile.id]?.message}
							</span>
						{/if}
					</button>
					<span
						class="hidden sm:flex items-center gap-1.5 shrink-0 text-[0.6875rem] text-gray-400 dark:text-gray-600"
					>
						<span class="w-1.5 h-1.5 rounded-full {statusTone(profile)}"></span>
						<span>{$t(statusKey(profile))}</span>
					</span>
					<ToggleSwitch value={profile.mode !== 'disabled'} onchange={() => toggleProfile(index)} />
				</div>
			{/each}
		</div>

		{#if profiles.length === 0}
			<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">
				{$t('admin.agentsNoProfiles')}
			</p>
		{/if}

		<div class="pt-4">
			<span class="text-xs text-gray-500">
				{profiles.length} profile{profiles.length === 1 ? '' : 's'}
			</span>
		</div>
	{/if}

	<div class="mt-5">
		<Models register={registerModels} />
	</div>

	<div class="pt-3 flex justify-end">
		<button type="button" class="settings-action" onclick={save} disabled={saving}>
			{#if saving}
				<Spinner size={14} />
			{:else}
				{$t('settings.save')}
			{/if}
		</button>
	</div>
</div>

{#if modal}
	<AgentProfileModal
		profile={modal.profile}
		isNew={modal.mode === 'create'}
		onclose={() => (modal = null)}
		onsave={saveProfile}
		ondelete={deleteProfile}
	/>
{/if}
