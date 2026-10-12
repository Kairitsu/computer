<script lang="ts">
	interface Props {
		value: boolean;
		onchange?: (value: boolean) => void;
		disabled?: boolean;
		ariaLabel?: string;
	}
	let { value, onchange, disabled = false, ariaLabel = 'Toggle' }: Props = $props();

	function toggle(event: MouseEvent) {
		event.stopPropagation();
		onchange?.(!value);
	}
</script>

<button
	type="button"
	class="settings-switch"
	class:is-on={value}
	onclick={toggle}
	{disabled}
	role="switch"
	aria-checked={value}
	aria-label={ariaLabel}
>
	<span class="knob"></span>
</button>

<style>
	.settings-switch {
		position: relative;
		width: 1.75rem;
		height: 1rem;
		flex-shrink: 0;
		border-radius: 999px;
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
		transition: background 0.15s;
	}

	.settings-switch.is-on {
		background: var(--app-accent);
	}

	.settings-switch:disabled {
		opacity: 0.4;
		pointer-events: none;
	}

	.knob {
		position: absolute;
		top: 0.125rem;
		left: 0.125rem;
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 999px;
		background: var(--app-bg);
		box-shadow: 0 1px 3px color-mix(in oklab, black 10%, transparent);
		transition:
			left 0.15s,
			background 0.15s;
	}

	.settings-switch.is-on .knob {
		left: 0.875rem;
		background: var(--app-accent-fg);
	}
</style>
