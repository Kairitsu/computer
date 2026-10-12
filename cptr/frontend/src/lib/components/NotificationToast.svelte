<script lang="ts">
	import { t } from '$lib/i18n';

	interface Props {
		onClick?: () => void;
		onclose?: () => void;
		title?: string;
		content?: string;
	}

	let { onClick = () => {}, onclose = () => {}, title = '', content = '' }: Props = $props();

	let closeButtonEl: HTMLButtonElement;
	let startX = 0;
	let startY = 0;
	let moved = false;
	const DRAG_THRESHOLD_PX = 6;

	function onPointerDown(e: PointerEvent) {
		startX = e.clientX;
		startY = e.clientY;
		moved = false;
		(e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
	}

	function onPointerMove(e: PointerEvent) {
		if (moved) return;
		const dx = e.clientX - startX;
		const dy = e.clientY - startY;
		if (dx * dx + dy * dy > DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) {
			moved = true;
		}
	}

	function onPointerUp(e: PointerEvent) {
		(e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
		if (closeButtonEl && (e.target === closeButtonEl || closeButtonEl.contains(e.target as Node))) {
			return;
		}
		if (!moved) {
			onClick();
		}
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	role="status"
	aria-live="polite"
	class="notification-toast app-theme app-surface group relative flex gap-2.5 text-left w-full border shadow-lg dark:shadow-none rounded-2xl py-3 px-4 cursor-pointer select-none"
	onpointerdown={onPointerDown}
	onpointermove={onPointerMove}
	onpointerup={onPointerUp}
	onpointercancel={() => (moved = true)}
>
	<!-- Close button -->
	<button
		bind:this={closeButtonEl}
		class="app-subtle-surface app-muted app-interactive absolute -top-0.5 -left-0.5 p-0.5 rounded-full opacity-0 group-hover:opacity-100 border-none cursor-pointer transition-opacity duration-150 z-10"
		aria-label={$t('a11y.dismissNotification')}
		onclick={(e) => {
			e.stopPropagation();
			onclose();
		}}
	>
		<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" class="w-3 h-3">
			<path
				d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
			/>
		</svg>
	</button>

	<!-- Icon -->
	<div class="shrink-0 self-start -translate-y-0.5">
		<img src="/favicon.png" alt="Computer" class="w-5 h-5 rounded-full" />
	</div>

	<!-- Content -->
	<div class="min-w-0">
		{#if title}
			<div
				class="text-[0.8125rem] font-medium mb-0.5 overflow-hidden text-ellipsis whitespace-nowrap"
			>
				{title}
			</div>
		{/if}
		{#if content}
			<div class="app-muted text-xs font-normal line-clamp-2">{content}</div>
		{/if}
	</div>
</div>

<style>
	.notification-toast {
		min-width: var(--width, 18.75rem);
	}
</style>
