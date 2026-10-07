<script lang="ts">
	import { onMount } from 'svelte';
	import Modal from './Modal.svelte';
	import SystemInfo from './SystemInfo.svelte';
	import Spinner from './common/Spinner.svelte';
	import { getHostNetwork, getWelcome, type HostNetwork } from '$lib/apis/state';
	import { t } from '$lib/i18n';

	interface Props {
		onclose: () => void;
	}

	let { onclose }: Props = $props();

	let loading = $state(true);
	let welcomeData = $state<{
		hostname?: string;
		system?: {
			arch: string;
			cpu_count: number;
			cpu_model?: string | null;
			cpu_virtual?: boolean | null;
			cpu_usage?: number;
			memory_total?: number;
			memory_available?: number;
			disk_total?: number;
			disk_used?: number;
			uptime_seconds?: number;
		};
		processes?: { pid: number; cpu: number; mem: number; name: string }[];
	} | null>(null);

	// The address lookup can leave the machine, so it loads on its own and
	// fills in once the stats are already showing.
	let network = $state<HostNetwork | null>(null);
	let networkPending = $state(true);

	onMount(() => {
		getWelcome()
			.then((data) => {
				welcomeData = data as typeof welcomeData;
			})
			.catch(() => {
				welcomeData = null;
			})
			.finally(() => {
				loading = false;
			});
		getHostNetwork()
			.then((data) => {
				network = data;
			})
			.catch(() => {
				network = null;
			})
			.finally(() => {
				networkPending = false;
			});
	});
</script>

<Modal {onclose} class="w-full max-w-[26.25rem] mx-4">
	<div class="max-h-[calc(100dvh-2rem)] overflow-y-auto px-4 py-3.5">
		<div class="mb-3.5 flex items-baseline justify-between gap-3">
			<div class="min-w-0">
				<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('system.infoTitle')}</h2>
				{#if welcomeData?.hostname}
					<p class="mt-0.5 truncate font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600">
						{welcomeData.hostname}
					</p>
				{/if}
			</div>
		</div>

		{#if loading}
			<div class="flex h-28 items-center justify-center">
				<Spinner size={18} />
			</div>
		{:else if welcomeData?.system}
			<SystemInfo
				system={welcomeData.system}
				processes={welcomeData.processes ?? []}
				{network}
				{networkPending}
			/>
		{:else}
			<div class="py-8 text-center text-xs text-gray-400 dark:text-gray-600">
				{$t('system.unavailable')}
			</div>
		{/if}
	</div>
</Modal>
