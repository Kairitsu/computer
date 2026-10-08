<script lang="ts">
	/**
	 * The OI mark as the sign-in page shows it: the glyph on a rounded tile.
	 * Light tile in light mode, graphite in dark mode; everything scales with `size`.
	 */
	interface Props {
		size?: number;
		title?: string;
		class?: string;
	}

	let { size = 20, title = '', class: className = '' }: Props = $props();
</script>

<span
	class="oi-mark {className}"
	style:--oi-size="{size}px"
	role={title ? 'img' : undefined}
	aria-label={title || undefined}
	aria-hidden={title ? undefined : 'true'}
>
	<svg viewBox="0 0 200 148" aria-hidden="true">
		<circle cx="74" cy="74" r="59.5" fill="none" stroke="currentColor" stroke-width="29" />
		<rect x="171" y="0" width="29" height="148" fill="currentColor" />
	</svg>
</span>

<style>
	.oi-mark {
		display: inline-flex;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		width: var(--oi-size);
		height: var(--oi-size);
		border-radius: calc(var(--oi-size) * 0.25);
		color: #0a0a0a;
		background: linear-gradient(180deg, #ffffff 0%, #f4f4f5 100%);
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.9),
			0 0 0 1px rgba(24, 24, 27, 0.08),
			0 1px 2px rgba(24, 24, 27, 0.06),
			0 calc(var(--oi-size) * 0.16) calc(var(--oi-size) * 0.4) calc(var(--oi-size) * -0.12)
				rgba(24, 24, 27, 0.24);
	}

	.oi-mark svg {
		width: 50%;
		height: auto;
	}

	:global(.dark) .oi-mark {
		color: #fafafa;
		background: linear-gradient(180deg, #2c2c30 0%, #1c1c1f 100%);
		box-shadow:
			inset 0 1px 0 rgba(255, 255, 255, 0.09),
			0 0 0 1px rgba(255, 255, 255, 0.08),
			0 1px 2px rgba(0, 0, 0, 0.3),
			0 calc(var(--oi-size) * 0.16) calc(var(--oi-size) * 0.4) calc(var(--oi-size) * -0.12)
				rgba(0, 0, 0, 0.6);
	}
</style>
