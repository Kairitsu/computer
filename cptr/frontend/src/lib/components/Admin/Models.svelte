<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getModelConfig,
		refreshModelList,
		updateModelConfig,
		getAdminConfig,
		updateConfig
	} from '$lib/apis/admin';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import { refreshChatState } from '$lib/stores/chat';
	import Icon from '../Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ModelSelector from '$lib/components/common/ModelSelector.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';

	type ModelEntry = {
		id: string;
		name: string;
		provider: string;
		is_active: boolean;
		systemPrompt: string;
		dirty: boolean;
	};

	let loading = $state(true);
	let saving = $state(false);
	let refreshing = $state(false);
	let models = $state<ModelEntry[]>([]);
	let selectedId = $state<string | null>(null);

	let globalSystemPrompt = $state('');
	let globalDirty = $state(false);
	let globalExpanded = $state(false);
	let showVariables = $state(false);

	// Default model
	let defaultModelId = $state('');

	const TEMPLATE_VARIABLES = [
		{ name: 'CPTR_CONTEXT', desc: 'Runtime, machine, workspace, and tool context' },
		{ name: 'RUNTIME_ENV', desc: 'Runtime environment (host or container)' },
		{ name: 'HOSTNAME', desc: 'Machine hostname' },
		{ name: 'WORKSPACE_NAME', desc: 'Workspace folder name' },
		{ name: 'WORKSPACE_PATH', desc: 'Full workspace path' },
		{ name: 'FILE_TREE', desc: 'File listing (top-level + 1 depth)' },
		{ name: 'INSTRUCTIONS', desc: 'MEMORY.md / AGENTS.md / CLAUDE.md content' },
		{ name: 'OS', desc: 'Operating system (macOS, Linux, Windows)' },
		{ name: 'PLATFORM', desc: 'Detailed platform string' },
		{ name: 'ARCH', desc: 'Machine architecture' },
		{ name: 'SHELL', desc: 'Default shell path' },
		{ name: 'HOME', desc: 'Home directory' },
		{ name: 'CPTR_VERSION', desc: 'Computer version' },
		{ name: 'DATE', desc: 'Current date (ISO format)' },
		{ name: 'MODEL', desc: 'Model ID being used' }
	];

	const DEFAULT_PROMPT_PLACEHOLDER = `You are Computer, a helpful assistant running inside the user's computer interface. You have access to tools to read, search, and modify files in the workspace, run commands, and use configured tools. Use them to help the user directly. Approach hard requests with initiative and persistence: make the best possible attempt, adapt as needed, and keep going unless a real constraint prevents progress.

{{CPTR_CONTEXT}}

{{INSTRUCTIONS}}

Workspace: {{WORKSPACE_NAME}}
Files:
{{FILE_TREE}}`;

	let hasDirty = $derived(globalDirty || models.some((m) => m.dirty));

	function applyModelConfig(
		data: Awaited<ReturnType<typeof getModelConfig>>,
		preserveDirty = false
	) {
		const config = data.config || {};
		const previousById = new Map(models.map((model) => [model.id, model]));

		if (!preserveDirty || !globalDirty) {
			globalSystemPrompt = config['*']?.params?.system_prompt || '';
			globalExpanded = !!globalSystemPrompt;
		}

		models = data.models.map((m) => {
			const previous = previousById.get(m.id);
			if (preserveDirty && previous?.dirty) {
				return { ...previous, ...m };
			}

			const mc = config[m.id];
			return {
				...m,
				is_active: mc?.is_active !== false,
				systemPrompt: mc?.params?.system_prompt || '',
				dirty: false
			};
		});

		if (selectedId && !models.some((model) => model.id === selectedId)) {
			selectedId = null;
		}
	}

	async function loadModelConfig() {
		try {
			applyModelConfig(await getModelConfig());
		} catch {
			toast.error($t('models.failedToLoad'));
		} finally {
			loading = false;
		}
	}

	onMount(async () => {
		await loadModelConfig();

		// Load default model
		try {
			const adminCfg = await getAdminConfig();
			defaultModelId =
				typeof adminCfg['chat.default_model'] === 'string' ? adminCfg['chat.default_model'] : '';
		} catch {}
	});

	async function refreshModels() {
		refreshing = true;
		try {
			applyModelConfig(await refreshModelList(), true);
			await refreshChatState();
			toast.success($t('models.refreshed'));
		} catch {
			toast.error($t('models.refreshFailed'));
		} finally {
			refreshing = false;
		}
	}

	async function toggleModel(model: ModelEntry, newVal: boolean) {
		model.is_active = newVal;
		models = [...models];
		try {
			await updateModelConfig(model.id, { is_active: newVal });
		} catch {
			model.is_active = !newVal;
			models = [...models];
			toast.error($t('models.failedToToggle'));
		}
	}

	function buildParams(systemPrompt: string): Record<string, unknown> {
		return systemPrompt.trim() ? { system_prompt: systemPrompt.trim() } : {};
	}

	async function saveAll() {
		saving = true;
		try {
			const promises: Promise<unknown>[] = [];
			if (globalDirty) {
				promises.push(updateModelConfig('*', { params: buildParams(globalSystemPrompt) }));
			}
			for (const model of models) {
				if (model.dirty) {
					promises.push(updateModelConfig(model.id, { params: buildParams(model.systemPrompt) }));
				}
			}
			await Promise.all(promises);
			globalDirty = false;
			models.forEach((m) => (m.dirty = false));

			await updateConfig({ 'chat.default_model': defaultModelId });

			toast.success($t('settings.saved'));
		} catch {
			toast.error($t('models.failedToSave'));
		} finally {
			saving = false;
		}
	}
</script>

{#snippet systemPromptField(value: string, onInput: (v: string) => void, placeholder: string)}
	<div class="mb-2">
		<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 uppercase tracking-wide"
			>{$t('models.systemPrompt')}</span
		>
		<textarea
			class="w-full mt-1 bg-gray-50 dark:bg-white/4 border border-gray-200 dark:border-white/8 rounded-lg px-2.5 py-2 text-[0.6875rem] font-mono text-gray-600 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none resize-y leading-relaxed"
			rows="6"
			{placeholder}
			{value}
			oninput={(e) => onInput((e.target as HTMLTextAreaElement).value)}
			spellcheck="false"
		></textarea>
		<div class="flex items-center gap-2 mt-1">
			<button
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
				onclick={() => (showVariables = !showVariables)}
			>
				{$t('models.templateVariables')}
				{showVariables ? '▾' : '▸'}
			</button>
			{#if value.trim()}
				<button
					class="text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
					onclick={() => onInput('')}
				>
					{$t('models.resetToDefault')}
				</button>
			{/if}
		</div>
		{#if showVariables}
			<div
				class="mt-1 rounded-lg bg-gray-50 dark:bg-white/3 border border-gray-100 dark:border-white/5 px-2.5 py-2"
			>
				{#each TEMPLATE_VARIABLES as v}
					<div class="flex items-baseline gap-2 h-5">
						<code
							class="text-[0.625rem] font-mono text-gray-500 dark:text-gray-500 shrink-0 select-all"
							>{`{{${v.name}}}`}</code
						>
						<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate">{v.desc}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

<div class="flex flex-col">
	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div>
			<div class="flex items-center justify-between mb-3">
				<h3 class="text-xs font-medium text-gray-700 dark:text-gray-300">
					{$t('admin.models')}
				</h3>
				<button
					class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 disabled:opacity-50 disabled:hover:text-gray-400 dark:text-gray-600 dark:hover:text-gray-300 dark:disabled:hover:text-gray-600 transition-colors duration-75"
					onclick={refreshModels}
					disabled={refreshing || saving}
					aria-label={$t('models.refresh')}
					use:tooltip={$t('models.refresh')}
				>
					<Icon name="refresh" size={13} class={refreshing ? 'animate-spin' : ''} />
				</button>
			</div>

			<div class="mb-1">
				<div class="flex items-center justify-between gap-3">
					<h3 class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
						{$t('models.defaultModel')}
					</h3>
					<div class="shrink-0">
						<ModelSelector bind:selectedModel={defaultModelId} preferAbove={false} />
					</div>
				</div>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
					{$t('models.defaultModelHint')}
				</p>
			</div>

			<!-- Global defaults -->
			<button
				class="group flex items-center gap-2 w-full h-7 text-left"
				onclick={() => (globalExpanded = !globalExpanded)}
			>
				<span class="flex-1 text-[0.8125rem] text-gray-500 dark:text-gray-400"
					>{$t('models.defaults')}</span
				>
				{#if globalSystemPrompt.trim()}
					<span class="text-[0.625rem] text-gray-400 dark:text-gray-600">prompt</span>
				{/if}
				<Icon
					name={globalExpanded ? 'chevron-down' : 'chevron-right'}
					size={10}
					class="shrink-0 text-gray-300 dark:text-gray-700"
				/>
			</button>

			{#if globalExpanded}
				{@render systemPromptField(
					globalSystemPrompt,
					(v) => {
						globalSystemPrompt = v;
						globalDirty = true;
					},
					DEFAULT_PROMPT_PLACEHOLDER
				)}
			{/if}

			<!-- Per-model list -->
			{#each models as model}
				<div class="group flex items-center gap-2 w-full h-7 text-left">
					<button
						type="button"
						class="flex-1 min-w-0 text-left text-[0.8125rem] truncate {model.is_active
							? 'text-gray-700 dark:text-gray-300'
							: 'text-gray-400 dark:text-gray-600'}"
						onclick={() => (selectedId = selectedId === model.id ? null : model.id)}
					>
						{model.name}
					</button>
					<ToggleSwitch value={model.is_active} onchange={(value) => toggleModel(model, value)} />
				</div>

				{#if selectedId === model.id}
					{@render systemPromptField(
						model.systemPrompt,
						(v) => {
							model.systemPrompt = v;
							model.dirty = true;
						},
						$t('models.systemPromptInherited')
					)}
				{/if}
			{/each}

			{#if models.length === 0}
				<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">
					{$t('models.noModels')}
				</p>
			{/if}
		</div>

		<div class="pt-3 flex justify-end">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100"
				onclick={saveAll}
			>
				{#if saving}{$t('settings.saving')}{:else}{$t('settings.save')}{/if}
			</button>
		</div>
	{/if}
</div>
