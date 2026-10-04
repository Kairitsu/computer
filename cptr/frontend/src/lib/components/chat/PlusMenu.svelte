<script lang="ts">
	import { onMount } from 'svelte';
	import { t } from '$lib/i18n';
	import Icon from '../Icon.svelte';
	import ToggleSwitch from '../common/ToggleSwitch.svelte';

	interface Props {
		onfiles: (files: FileList) => void;
		oncapture?: (file: File) => void;
		planMode?: boolean;
		requestParams?: Record<string, unknown>;
		onchange?: () => void;
	}
	let {
		onfiles,
		oncapture,
		planMode = $bindable(false),
		requestParams = $bindable({}),
		onchange
	}: Props = $props();

	let open = $state(false);
	let tab = $state<'' | 'request_params'>('');
	let btnEl: HTMLButtonElement | undefined = $state();
	let menuEl: HTMLDivElement | undefined = $state();
	let inputEl: HTMLInputElement | undefined = $state();
	let cameraInputEl: HTMLInputElement | undefined = $state();
	let pos = $state<{ x: number; bottom: number }>({ x: -9999, bottom: -9999 });
	let ready = $state(false);

	// ── Request params state ────────────────────────
	let paramRows = $state<Array<{ key: string; value: string }>>(
		Object.entries(requestParams).map(([key, value]) => ({
			key,
			value: typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
		}))
	);

	const paramCount = $derived(paramRows.filter((r) => r.key.trim()).length);

	const canAddParam = $derived(
		paramRows.length === 0 || paramRows.every((r) => r.key.trim() || r.value.trim())
	);

	function addParamRow() {
		paramRows = [...paramRows, { key: '', value: '' }];
	}

	function removeParamRow(index: number) {
		paramRows = paramRows.filter((_, i) => i !== index);
		syncParams();
	}

	function syncParams() {
		const result: Record<string, unknown> = {};
		for (const { key, value } of paramRows) {
			if (!key.trim()) continue;
			try {
				result[key.trim()] = JSON.parse(value);
			} catch {
				result[key.trim()] = value;
			}
		}
		requestParams = result;
		onchange?.();
	}

	$effect(() => {
		paramRows = Object.entries(requestParams).map(([key, value]) => ({
			key,
			value: typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
		}));
	});

	function toggle() {
		open = !open;
		if (open) {
			tab = '';
			requestAnimationFrame(updatePosition);
		}
	}

	function updatePosition() {
		if (!btnEl || !menuEl) return;
		const rect = btnEl.getBoundingClientRect();
		const mw = menuEl.offsetWidth;
		const vh = window.innerHeight;
		let x = rect.left;
		if (x + mw > window.innerWidth - 8) x = window.innerWidth - mw - 8;
		if (x < 8) x = 8;
		pos = { x, bottom: vh - rect.top + 4 };
		ready = true;
	}

	function triggerUpload() {
		open = false;
		inputEl?.click();
	}

	function handleFileChange() {
		if (inputEl?.files && inputEl.files.length > 0) {
			onfiles(inputEl.files);
			inputEl.value = '';
		}
	}

	function handleCameraChange() {
		if (cameraInputEl?.files && cameraInputEl.files.length > 0) {
			// Camera input returns image files — pass them through oncapture or onfiles
			for (const file of Array.from(cameraInputEl.files)) {
				if (oncapture) oncapture(file);
			}
			cameraInputEl.value = '';
		}
	}

	function isMobileDevice(): boolean {
		if (typeof navigator === 'undefined') return false;
		const ua = navigator.userAgent || (navigator as any).vendor || (window as any).opera || '';
		return /android|iphone|ipad|ipod|windows phone/i.test(ua);
	}

	async function captureHandler() {
		open = false;
		if (isMobileDevice()) {
			// On mobile, open the camera
			cameraInputEl?.click();
			return;
		}
		// Desktop: use screen capture API
		try {
			const mediaStream = await navigator.mediaDevices.getDisplayMedia({
				video: { cursor: 'never' } as any,
				audio: false
			});
			const video = document.createElement('video');
			video.srcObject = mediaStream;
			await video.play();

			const canvas = document.createElement('canvas');
			canvas.width = video.videoWidth;
			canvas.height = video.videoHeight;
			const ctx = canvas.getContext('2d');
			if (ctx) ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

			mediaStream.getTracks().forEach((t) => t.stop());
			window.focus();

			const blob = await new Promise<Blob | null>((res) => canvas.toBlob(res, 'image/png'));
			if (blob) {
				const file = new File([blob], `screen-capture-${Date.now()}.png`, { type: 'image/png' });
				if (oncapture) oncapture(file);
			}
			video.srcObject = null;
		} catch (err) {
			console.error('Error capturing screen:', err);
		}
	}

	function close() {
		open = false;
		ready = false;
		tab = '';
	}

	$effect(() => {
		if (!open) {
			ready = false;
			tab = '';
			return;
		}
		// Keep menu anchored on resize/scroll while open
		window.addEventListener('resize', updatePosition);
		window.addEventListener('scroll', updatePosition, true);
		return () => {
			window.removeEventListener('resize', updatePosition);
			window.removeEventListener('scroll', updatePosition, true);
		};
	});
</script>

<input
	bind:this={inputEl}
	type="file"
	multiple
	accept="image/*,.pdf,.txt,.md,.csv,.json,.xml,.yaml,.yml,.html,.css,.js,.ts,.py,.go,.rs,.java,.c,.cpp,.h,.rb,.sh"
	class="hidden"
	onchange={handleFileChange}
/>

<!-- Hidden camera input for mobile capture -->
<input
	bind:this={cameraInputEl}
	type="file"
	accept="image/*"
	capture="environment"
	class="hidden"
	onchange={handleCameraChange}
/>

<button
	bind:this={btnEl}
	type="button"
	class="flex items-center justify-center w-6 h-6 rounded-full text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition-colors duration-100 cursor-pointer"
	onclick={toggle}
	aria-expanded={open}
	aria-haspopup="menu"
>
	<svg
		class="size-3.5 transition-transform duration-150"
		class:rotate-45={open}
		viewBox="0 0 24 24"
		fill="none"
		stroke="currentColor"
		stroke-width="2"
		stroke-linecap="round"
		stroke-linejoin="round"
	>
		<line x1="12" y1="5" x2="12" y2="19" />
		<line x1="5" y1="12" x2="19" y2="12" />
	</svg>
</button>

{#if open}
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="fixed inset-0 z-[100]" onclick={close}></div>

	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		bind:this={menuEl}
		class="app-theme app-surface fixed z-[101] w-56 rounded-xl border shadow-xl p-0.5 overflow-hidden"
		style="left: {pos.x}px; bottom: {pos.bottom}px; opacity: {ready ? 1 : 0}; pointer-events: {ready
			? 'auto'
			: 'none'};"
		onclick={(e) => e.stopPropagation()}
	>
		{#if tab === ''}
			<!-- Main menu -->
			<div class="plus-menu-slide-in-left">
				<button
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors duration-75"
					onclick={triggerUpload}
				>
					<svg
						class="size-3.5 shrink-0"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.5"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path
							d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"
						/>
					</svg>
					<span class="flex-1 text-left truncate">{$t('plusMenu.attachFiles')}</span>
				</button>

				<button
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors duration-75"
					onclick={captureHandler}
				>
					<svg
						class="size-3.5 shrink-0"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.5"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path
							d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"
						/>
						<circle cx="12" cy="13" r="4" />
					</svg>
					<span class="flex-1 text-left truncate">{$t('plusMenu.capture')}</span>
				</button>

				<div class="app-divider h-px mx-1 my-0.5"></div>

				<!-- Plan mode toggle -->
				<button
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors duration-75"
					onclick={() => {
						planMode = !planMode;
						onchange?.();
					}}
				>
					<svg
						class="size-3.5 shrink-0"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.5"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
						<polyline points="14 2 14 8 20 8" />
						<line x1="9" y1="13" x2="15" y2="13" />
						<line x1="9" y1="17" x2="15" y2="17" />
					</svg>
					<span class="flex-1 text-left truncate">{$t('plusMenu.planMode')}</span>
					<ToggleSwitch
						value={planMode}
						onchange={(v) => {
							planMode = v;
							onchange?.();
						}}
					/>
				</button>

				<div class="app-divider h-px mx-1 my-0.5"></div>

				<button
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors duration-75"
					onclick={() => (tab = 'request_params')}
				>
					<svg
						class="size-3.5 shrink-0"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="1.5"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<circle cx="12" cy="12" r="3" />
						<path
							d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"
						/>
					</svg>
					<span class="flex-1 text-left truncate">{$t('plusMenu.parameters')}</span>
					{#if paramCount > 0}
						<span class="text-[0.625rem] text-gray-400 dark:text-gray-500">{paramCount}</span>
					{/if}
					<Icon name="chevron-right" size={12} class="shrink-0 text-gray-400 dark:text-gray-500" />
				</button>
			</div>
		{:else if tab === 'request_params'}
			<!-- Request params submenu -->
			<div class="plus-menu-slide-in-right">
				<button
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors duration-75"
					onclick={() => (tab = '')}
				>
					<Icon name="chevron-left" size={12} />
					<span class="flex-1 text-left font-medium">{$t('plusMenu.parameters')}</span>
				</button>

				<div class="app-divider h-px mx-1 my-0.5"></div>

				{#if paramRows.length === 0}
					<p
						class="px-2 h-7 flex items-center justify-center text-[0.6875rem] text-gray-400 dark:text-gray-500"
					>
						{$t('plusMenu.noParams')}
					</p>
				{:else}
					<div class="px-0.5 py-0.5 flex flex-col max-h-48 overflow-y-auto">
						{#each paramRows as row, i}
							<div class="group/row flex items-center gap-1 px-1.5 h-6">
								<input
									type="text"
									placeholder={$t('plusMenu.paramKey')}
									bind:value={row.key}
									onblur={syncParams}
									autocomplete="off"
									spellcheck="false"
									class="w-[4.5rem] shrink-0 bg-transparent text-[0.6875rem] font-mono text-gray-600 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
								/>
								<input
									type="text"
									placeholder={$t('plusMenu.paramValue')}
									bind:value={row.value}
									onblur={syncParams}
									autocomplete="off"
									spellcheck="false"
									class="flex-1 min-w-0 bg-transparent text-[0.6875rem] font-mono text-gray-600 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
								/>
								<button
									type="button"
									onclick={() => removeParamRow(i)}
									class="shrink-0 text-gray-300 dark:text-gray-700 opacity-0 group-hover/row:opacity-100 hover:text-gray-500 dark:hover:text-gray-400 transition-colors duration-75"
									aria-label={$t('plusMenu.removeParam')}
								>
									<Icon name="xmark" size={8} />
								</button>
							</div>
						{/each}
					</div>
				{/if}

				<button
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 hover:text-gray-900 dark:hover:text-white transition-colors duration-75 disabled:opacity-30 disabled:pointer-events-none"
					onclick={addParamRow}
					disabled={!canAddParam}
				>
					<Icon name="plus" size={12} />
					<span class="flex-1 text-left">{$t('plusMenu.addParam')}</span>
				</button>
			</div>
		{/if}
	</div>
{/if}

<style>
	@reference "../../../app.css";

	.plus-menu-slide-in-left {
		animation: slideFromLeft 0.15s ease-out;
	}

	.plus-menu-slide-in-right {
		animation: slideFromRight 0.15s ease-out;
	}

	@keyframes slideFromLeft {
		from {
			opacity: 0;
			transform: translateX(-0.75rem);
		}
		to {
			opacity: 1;
			transform: translateX(0);
		}
	}

	@keyframes slideFromRight {
		from {
			opacity: 0;
			transform: translateX(0.75rem);
		}
		to {
			opacity: 1;
			transform: translateX(0);
		}
	}
</style>
