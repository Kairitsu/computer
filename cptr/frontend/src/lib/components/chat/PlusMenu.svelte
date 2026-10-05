<script lang="ts">
	import { t } from '$lib/i18n';
	import ToggleSwitch from '../common/ToggleSwitch.svelte';

	interface Props {
		onfiles: (files: FileList) => void;
		planMode?: boolean;
		onchange?: () => void;
	}
	let { onfiles, planMode = $bindable(false), onchange }: Props = $props();

	let open = $state(false);
	let btnEl: HTMLButtonElement | undefined = $state();
	let menuEl: HTMLDivElement | undefined = $state();
	let inputEl: HTMLInputElement | undefined = $state();
	let pos = $state<{ x: number; bottom: number }>({ x: -9999, bottom: -9999 });
	let ready = $state(false);

	function toggle() {
		open = !open;
		if (open) requestAnimationFrame(updatePosition);
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

	function close() {
		open = false;
		ready = false;
	}

	$effect(() => {
		if (!open) {
			ready = false;
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
		</div>
	</div>
{/if}

<style>
	@reference "../../../app.css";

	.plus-menu-slide-in-left {
		animation: slideFromLeft 0.15s ease-out;
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
</style>
