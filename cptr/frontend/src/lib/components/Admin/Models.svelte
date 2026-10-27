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
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ModelSelector from '$lib/components/common/ModelSelector.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';

	interface ModelsApi {
		refresh: () => Promise<void>;
		save: () => Promise<void>;
	}

	let { register }: { register?: (api: ModelsApi) => void } = $props();

	type ModelEntry = {
		id: string;
		name: string;
		provider: string;
		profile_name?: string;
		is_active: boolean;
	};

	let loading = $state(true);
	let models = $state<ModelEntry[]>([]);

	// Default model
	let defaultModelId = $state('');
	let defaultModelLoaded = false;

	const modelGroups = $derived.by(() => {
		const groups: { name: string; models: ModelEntry[] }[] = [];
		for (const model of models) {
			const name = model.profile_name || '';
			const last = groups.at(-1);
			if (!last || last.name !== name) groups.push({ name, models: [model] });
			else last.models.push(model);
		}
		return groups;
	});

	// The agents run on their own system prompts, so models only have an on/off switch.
	function applyModelConfig(data: Awaited<ReturnType<typeof getModelConfig>>) {
		const config = data.config || {};
		models = data.models.map((m) => ({ ...m, is_active: config[m.id]?.is_active !== false }));
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
			defaultModelLoaded = true;
		} catch {}
	});

	async function refreshModels() {
		applyModelConfig(await refreshModelList());
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

	async function saveAll() {
		// Skip until the stored default has loaded, so an early save cannot clear it.
		if (defaultModelLoaded) {
			await updateConfig({ 'chat.default_model': defaultModelId });
		}
	}

	$effect(() => {
		register?.({ refresh: refreshModels, save: saveAll });
	});
</script>

<div class="flex flex-col">
	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div>
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

			<!-- Per-model list -->
			<div class="mt-1.5 flex flex-col gap-1.5">
				{#each modelGroups as group (group.name)}
					{#if group.name}
						<p class="px-2.5 pt-1 text-[0.625rem] font-semibold text-gray-400">{group.name}</p>
					{/if}
				{#each group.models as model}
					<div class="settings-row flex items-center gap-2 w-full min-h-9 px-2.5 py-1.5 text-left">
						<span
							class="flex-1 min-w-0 text-left text-[0.8125rem] truncate {model.is_active
								? 'text-gray-700 dark:text-gray-300'
								: 'text-gray-400 dark:text-gray-600'}"
						>
							{model.name}
						</span>
						<ToggleSwitch value={model.is_active} onchange={(value) => toggleModel(model, value)} />
					</div>
				{/each}
				{/each}
			</div>

			{#if models.length === 0}
				<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">
					{$t('models.noModels')}
				</p>
			{/if}
		</div>
	{/if}
</div>
