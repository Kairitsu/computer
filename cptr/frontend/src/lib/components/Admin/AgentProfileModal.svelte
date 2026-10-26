<script lang="ts">
	import Modal from '../Modal.svelte';
	import { t } from '$lib/i18n';
	import type { AgentProfile } from '$lib/apis/admin';

	interface Props {
		profile: AgentProfile;
		isNew?: boolean;
		onclose: () => void;
		onsave: (profile: AgentProfile) => void;
		ondelete: () => void;
	}

	let { profile, isNew = false, onclose, onsave, ondelete }: Props = $props();
	const fieldPrefix = `agent-profile-${Math.random().toString(36).slice(2)}`;

	// svelte-ignore state_referenced_locally
	let draft = $state<AgentProfile>({
		...profile,
		models: [...(profile.models?.length ? profile.models : [])]
	});

	function normalizeDraft(): AgentProfile {
		const currentModels = draft.models?.map((model) => model.trim()).filter(Boolean) || [];
		const models = currentModels.filter((model) => model !== 'default');
		const defaultModel = models.includes(draft.default_model)
			? draft.default_model
			: models[0] || '';
		return {
			...draft,
			id: draft.id.trim(),
			name: draft.name.trim() || draft.id.trim(),
			command: draft.command.trim() || defaultCommand(draft.agent),
			home: draft.home?.trim() || null,
			models,
			default_model: defaultModel
		};
	}

	function defaultCommand(agent: AgentProfile['agent']): string {
		// The AnyRouter profile is fixed on the server, which sends its own install's path.
		return { grok: 'grok', claude: profile.command }[agent];
	}

	function defaultName(agent: AgentProfile['agent']): string {
		return { grok: 'Grok', claude: 'AnyRouter' }[agent];
	}

	const locked = $derived(draft.id === 'anyrouter' || draft.agent === 'claude');
</script>

<Modal {onclose} class="w-full max-w-md mx-4">
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="p-4"
		onkeydown={(e) => {
			if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onsave(normalizeDraft());
		}}
	>
		<div class="flex items-center justify-between gap-3 mb-3">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">
				{isNew ? $t('admin.agentsAddProfile') : draft.name}
			</h2>
			<button type="button" class="settings-action" onclick={onclose}> Close </button>
		</div>

		<div class="flex gap-3">
			<div class="flex-1">
				<label for={`${fieldPrefix}-name`} class="text-[0.625rem] text-gray-400 dark:text-gray-600">
					{$t('admin.agentsProfileName')}
				</label>
				<input
					id={`${fieldPrefix}-name`}
					type="text"
					bind:value={draft.name}
					autocomplete="off"
					spellcheck="false"
					disabled={locked}
					class="settings-field mt-1 w-full"
				/>
			</div>
			<div class="w-32 shrink-0">
				<label for={`${fieldPrefix}-type`} class="text-[0.625rem] text-gray-400 dark:text-gray-600">
					{$t('admin.agentsType')}
				</label>
				<select
					id={`${fieldPrefix}-type`}
					value={draft.agent}
					disabled={locked}
					onchange={(e) => {
						const agent = e.currentTarget.value as AgentProfile['agent'];
						draft = {
							...draft,
							agent,
							name: defaultName(agent),
							command: defaultCommand(agent),
							models: [],
							default_model: ''
						};
					}}
					class="settings-field mt-1 w-full"
				>
					<option value="grok">Grok</option>
					{#if draft.agent === 'claude'}
						<option value="claude">AnyRouter</option>
					{/if}
				</select>
			</div>
		</div>

		<label for={`${fieldPrefix}-id`} class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
			{$t('admin.agentsProfileId')}
		</label>
		<input
			id={`${fieldPrefix}-id`}
			type="text"
			bind:value={draft.id}
			autocomplete="off"
			spellcheck="false"
			disabled={locked}
			class="settings-field mt-1 w-full font-mono"
		/>

		<label
			for={`${fieldPrefix}-command`}
			class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
		>
			{$t('admin.agentsCommand')}
		</label>
		<input
			id={`${fieldPrefix}-command`}
			type="text"
			bind:value={draft.command}
			placeholder={defaultCommand(draft.agent)}
			autocomplete="off"
			spellcheck="false"
			disabled={locked}
			class="settings-field mt-1 w-full font-mono"
		/>

		<label
			for={`${fieldPrefix}-home`}
			class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
		>
			{$t('admin.agentsHome')}
		</label>
		<input
			id={`${fieldPrefix}-home`}
			type="text"
			value={draft.home || ''}
			placeholder={$t('admin.optional')}
			autocomplete="off"
			spellcheck="false"
			disabled={locked}
			oninput={(e) => (draft.home = e.currentTarget.value || null)}
			class="settings-field mt-1 w-full font-mono"
		/>


		<div class="flex items-center justify-between mt-3">
			<div class="flex items-center gap-3">
				{#if !isNew && !locked}
					<button
						type="button"
						class="settings-action danger"
						onclick={ondelete}
						title={$t('admin.agentsDeleteProfile')}
					>
						{$t('admin.agentsDeleteProfile')}
					</button>
				{/if}
			</div>
			<button type="button" class="settings-action" onclick={() => onsave(normalizeDraft())}>
				{$t('settings.save')}
			</button>
		</div>
	</div>
</Modal>
