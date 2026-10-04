<script lang="ts">
	/**
	 * Floating panel anchored to a trigger, used by the composer chips.
	 * Prefers the requested side, flips when it does not fit, and clamps
	 * horizontally to the viewport. Closes on outside click or Escape.
	 */
	import type { Snippet } from 'svelte';

	interface Props {
		anchor: HTMLElement;
		align?: 'start' | 'end';
		placement?: 'above' | 'below';
		width?: string;
		class?: string;
		onclose: () => void;
		children: Snippet;
	}

	let {
		anchor,
		align = 'start',
		placement = 'above',
		width = '15rem',
		class: className = '',
		onclose,
		children
	}: Props = $props();

	const GAP = 6;
	const EDGE = 8;

	let panelEl: HTMLDivElement | undefined = $state();
	let style = $state('left: -9999px; top: -9999px;');
	let ready = $state(false);

	function update() {
		if (!anchor || !panelEl) return;
		const rect = anchor.getBoundingClientRect();
		const vw = window.innerWidth;
		const vh = window.innerHeight;
		const panelWidth = panelEl.offsetWidth;
		const panelHeight = panelEl.scrollHeight;
		let left = align === 'end' ? rect.right - panelWidth : rect.left;
		left = Math.max(EDGE, Math.min(left, vw - panelWidth - EDGE));
		const spaceAbove = rect.top - GAP - EDGE;
		const spaceBelow = vh - rect.bottom - GAP - EDGE;
		const above =
			placement === 'above'
				? panelHeight <= spaceAbove || spaceAbove >= spaceBelow
				: !(panelHeight <= spaceBelow || spaceBelow >= spaceAbove);
		style = above
			? `left: ${left}px; bottom: ${vh - rect.top + GAP}px; max-height: ${spaceAbove}px;`
			: `left: ${left}px; top: ${rect.bottom + GAP}px; max-height: ${spaceBelow}px;`;
		ready = true;
	}

	$effect(() => {
		if (!panelEl) return;
		const frame = requestAnimationFrame(update);
		const observer = new ResizeObserver(update);
		observer.observe(panelEl);
		window.addEventListener('resize', update);
		window.addEventListener('scroll', update, true);
		return () => {
			cancelAnimationFrame(frame);
			observer.disconnect();
			window.removeEventListener('resize', update);
			window.removeEventListener('scroll', update, true);
		};
	});

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.stopPropagation();
			onclose();
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
<div class="fixed inset-0 z-[100]" onclick={onclose}></div>

<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
<div
	bind:this={panelEl}
	class="popover-panel app-theme fixed z-[101] overflow-y-auto overflow-x-hidden rounded-xl p-1 shadow-xl {className}"
	style="{style} width: {width}; opacity: {ready ? 1 : 0}; pointer-events: {ready
		? 'auto'
		: 'none'};"
	onclick={(event) => event.stopPropagation()}
>
	{@render children()}
</div>

<style>
	/* Raised above the page so menus read as a surface in dark themes too. */
	.popover-panel {
		background: color-mix(in oklab, var(--app-bg) 94%, var(--app-fg));
		color: var(--app-fg);
		border: 1px solid color-mix(in oklab, var(--app-fg) 12%, transparent);
		box-shadow:
			0 12px 32px color-mix(in oklab, black 22%, transparent),
			0 2px 6px color-mix(in oklab, black 10%, transparent);
	}
</style>
