<script lang="ts">
	/**
	 * CHANGELOG.md versions as parsed by the backend (cptr/utils/changelog.py).
	 * Used by the changelog modal and by the update dialog's release notes.
	 */
	import { t, locale } from '$lib/i18n';
	import type { ChangelogVersion } from '$lib/apis/update';

	interface Props {
		versions: [string, ChangelogVersion][];
	}

	let { versions }: Props = $props();

	const SECTION_TONES: Record<string, string> = {
		added: 'bg-blue-500/10 text-blue-600 dark:text-blue-400',
		changed: 'bg-yellow-500/10 text-yellow-700 dark:text-yellow-400',
		fixed: 'bg-green-500/10 text-green-600 dark:text-green-400',
		removed: 'bg-red-500/10 text-red-600 dark:text-red-400'
	};

	function sectionLabel(section: string): string {
		return section in SECTION_TONES ? $t(`changelog.section.${section}`) : section;
	}

	function formatDate(value: string): string {
		const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
		if (!match) return value;
		const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
		try {
			return new Intl.DateTimeFormat($locale, { dateStyle: 'long' }).format(date);
		} catch {
			return value;
		}
	}
</script>

<div class="space-y-6">
	{#each versions as [ver, data] (ver)}
		<div>
			<div class="mb-2 flex items-baseline gap-2">
				<h3 class="text-[0.8125rem] font-semibold text-gray-900 dark:text-white">v{ver}</h3>
				<span class="text-[0.6875rem] text-gray-400 dark:text-gray-500"
					>{formatDate(data.date)}</span
				>
			</div>

			{#each Object.entries(data).filter(([key]) => key !== 'date') as [section, items] (section)}
				{#if Array.isArray(items) && items.length > 0}
					<div class="mb-3">
						<span
							class="my-1.5 inline-block rounded-full px-2 py-0.5 text-[0.6875rem] font-semibold tracking-wide {SECTION_TONES[
								section
							] ?? 'text-gray-400 dark:text-gray-600'}"
						>
							{sectionLabel(section)}
						</span>
						<ul class="mt-1.5 space-y-2">
							{#each items as entry, i (i)}
								<li class="flex gap-2.5 text-xs leading-relaxed">
									<span
										class="mt-[0.5em] h-1 w-1 shrink-0 rounded-full bg-gray-300 dark:bg-gray-700"
									></span>
									<div class="min-w-0">
										{#if entry.title}
											<span class="font-medium text-gray-900 dark:text-white">{entry.title}</span>
											{#if entry.content}
												<span class="ml-1 text-gray-500 dark:text-gray-400">{entry.content}</span>
											{/if}
										{:else}
											<span class="text-gray-600 dark:text-gray-400"
												>{entry.content || entry.raw}</span
											>
										{/if}
									</div>
								</li>
							{/each}
						</ul>
					</div>
				{/if}
			{/each}
		</div>
	{/each}
</div>
