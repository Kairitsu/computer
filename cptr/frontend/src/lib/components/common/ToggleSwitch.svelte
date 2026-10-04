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
	class="relative w-7 h-4 rounded-full transition-colors duration-150 shrink-0
		{value ? '' : 'bg-gray-300 dark:bg-gray-700'}"
	style={value ? 'background: var(--app-accent);' : undefined}
	onclick={toggle}
	{disabled}
	role="switch"
	aria-checked={value}
	aria-label={ariaLabel}
>
	<span
		class="absolute top-[0.125rem] w-3 h-3 rounded-full transition-all duration-150
			{value ? 'left-[0.875rem]' : 'left-[0.125rem] bg-white dark:bg-gray-500'}"
		style={value ? 'background: var(--app-accent-fg);' : undefined}
	></span>
</button>
