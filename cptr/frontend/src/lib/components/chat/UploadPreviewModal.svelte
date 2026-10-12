<script lang="ts">
	import { fetchHandler } from '$lib/apis';
	import Icon from '$lib/components/Icon.svelte';
	import Modal from '$lib/components/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import MarkdownRenderer from '$lib/components/markdown/MarkdownRenderer.svelte';
	import CsvTable from '$lib/components/preview/CsvTable.svelte';
	import ImagePreview from '$lib/components/preview/ImagePreview.svelte';
	import JsonTreeView from '$lib/components/preview/JsonTreeView.svelte';
	import OfficePreview from '$lib/components/preview/OfficePreview.svelte';
	import PDFViewer from '$lib/components/preview/PDFViewer.svelte';
	import SvgPreview from '$lib/components/preview/SvgPreview.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		name: string;
		url: string;
		type: string;
		onclose: () => void;
	}

	let { name, url, type, onclose }: Props = $props();

	type Kind =
		| 'image'
		| 'pdf'
		| 'office'
		| 'html'
		| 'svg'
		| 'markdown'
		| 'json'
		| 'csv'
		| 'text'
		| 'audio'
		| 'video'
		| 'unsupported';

	const MAX_TEXT_BYTES = 200_000;
	const IMAGE_EXT = new Set([
		'png',
		'jpg',
		'jpeg',
		'gif',
		'webp',
		'bmp',
		'avif',
		'ico',
		'heic',
		'heif'
	]);
	const OFFICE_EXT = new Set(['docx', 'xlsx', 'xls', 'pptx']);
	const AUDIO_EXT = new Set(['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac', 'opus']);
	const VIDEO_EXT = new Set(['mp4', 'webm', 'mov', 'm4v', 'ogv']);
	const TEXT_EXT = new Set([
		'txt',
		'text',
		'log',
		'py',
		'js',
		'ts',
		'tsx',
		'jsx',
		'mjs',
		'cjs',
		'css',
		'scss',
		'svelte',
		'vue',
		'rs',
		'go',
		'java',
		'c',
		'h',
		'cpp',
		'hpp',
		'cc',
		'sh',
		'bash',
		'zsh',
		'yml',
		'yaml',
		'toml',
		'ini',
		'cfg',
		'conf',
		'xml',
		'sql',
		'rb',
		'php',
		'jsonl',
		'env',
		'rst',
		'tex',
		'diff',
		'patch',
		'kt',
		'swift',
		'lua',
		'pl',
		'r',
		'gradle',
		'gitignore',
		'dockerignore',
		'editorconfig',
		'makefile',
		'dockerfile',
		'gemfile',
		'rakefile'
	]);

	function extOf(fileName: string): string {
		const base = (fileName.split(/[/\\]/).pop() ?? fileName).toLowerCase();
		if (base.startsWith('.') && !base.slice(1).includes('.')) return base.slice(1);
		const dot = base.lastIndexOf('.');
		return dot > 0 ? base.slice(dot + 1) : '';
	}

	function previewKind(fileName: string, fileType: string): Kind {
		const ext = extOf(fileName);
		if (ext === 'svg') return 'svg';
		if (fileType === 'image' || IMAGE_EXT.has(ext)) return 'image';
		if (ext === 'pdf') return 'pdf';
		if (OFFICE_EXT.has(ext)) return 'office';
		if (ext === 'html' || ext === 'htm') return 'html';
		if (ext === 'md' || ext === 'markdown') return 'markdown';
		if (ext === 'json') return 'json';
		if (ext === 'csv' || ext === 'tsv') return 'csv';
		if (AUDIO_EXT.has(ext)) return 'audio';
		if (VIDEO_EXT.has(ext)) return 'video';
		if (TEXT_EXT.has(ext)) return 'text';
		return 'unsupported';
	}

	const kind = $derived(previewKind(name, type));
	const needsText = $derived(['svg', 'markdown', 'json', 'csv', 'text'].includes(kind));

	let loading = $state(false);
	let error = $state('');
	let content = $state('');
	let jsonData = $state<unknown>(null);
	let truncated = $state(false);

	$effect(() => {
		const currentUrl = url;
		const currentKind = kind;
		let cancelled = false;
		content = '';
		jsonData = null;
		error = '';
		truncated = false;
		if (!needsText) return;

		loading = true;
		void (async () => {
			try {
				const res = await fetchHandler(currentUrl);
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				const bytes = new Uint8Array(await res.arrayBuffer());
				if (cancelled) return;
				const cut = bytes.length > MAX_TEXT_BYTES;
				const text = new TextDecoder().decode(cut ? bytes.slice(0, MAX_TEXT_BYTES) : bytes);
				content = text;
				truncated = cut;
				if (currentKind === 'json' && !cut) {
					try {
						jsonData = JSON.parse(text);
					} catch {
						jsonData = null;
					}
				}
			} catch (e) {
				if (!cancelled) error = e instanceof Error ? e.message : '';
			} finally {
				if (!cancelled) loading = false;
			}
		})();

		return () => {
			cancelled = true;
		};
	});
</script>

<Modal
	{onclose}
	class="upload-preview w-full max-w-3xl lg:max-w-4xl xl:max-w-5xl mx-4 md:mx-0 flex flex-col overflow-hidden h-[85vh] max-h-[85vh] lg:max-h-[90vh] md:h-[35rem] lg:h-[42rem] xl:h-[46rem]"
>
	<header
		class="flex items-center gap-2 shrink-0 h-11 pl-4 pr-2 border-b border-gray-200 dark:border-white/6"
	>
		<h2 class="min-w-0 flex-1 truncate text-sm font-medium">{name}</h2>
		<button type="button" class="settings-icon" onclick={onclose} aria-label={$t('common.close')}>
			<Icon name="xmark" size={12} />
		</button>
	</header>

	<div class="preview-stage">
		{#if loading}
			<div class="center-note"><Spinner size={20} /></div>
		{:else if error}
			<div class="center-note">{$t('preview.failedToLoad')}</div>
		{:else if kind === 'image'}
			<div class="fill"><ImagePreview src={url} alt={name} /></div>
		{:else if kind === 'pdf'}
			<div class="fill"><PDFViewer src={url} /></div>
		{:else if kind === 'office'}
			<div class="fill"><OfficePreview src={url} fileName={name} /></div>
		{:else if kind === 'html'}
			<iframe class="fill-frame" src={url} title={name} sandbox="allow-scripts"></iframe>
		{:else if kind === 'svg' && content}
			<div class="fill"><SvgPreview {content} /></div>
		{:else if kind === 'markdown' && content}
			<div class="scroll-preview">
				{#if truncated}<p class="truncate-note">{$t('preview.truncated')}</p>{/if}
				<MarkdownRenderer {content} />
			</div>
		{:else if kind === 'json' && jsonData !== null}
			<div class="scroll-preview mono">
				<JsonTreeView data={jsonData} />
			</div>
		{:else if kind === 'csv' && content}
			<div class="scroll-preview">
				{#if truncated}<p class="truncate-note">{$t('preview.truncated')}</p>{/if}
				<CsvTable {content} separator={name.toLowerCase().endsWith('.tsv') ? '\t' : ','} />
			</div>
		{:else if (kind === 'text' || kind === 'json' || kind === 'svg') && content}
			<div class="scroll-preview mono">
				{#if truncated}<p class="truncate-note">{$t('preview.truncated')}</p>{/if}
				<pre>{content}</pre>
			</div>
		{:else if kind === 'audio'}
			<div class="center-note">
				<audio controls preload="metadata" src={url}></audio>
			</div>
		{:else if kind === 'video'}
			<div class="center-note video-note">
				<!-- svelte-ignore a11y_media_has_caption -->
				<video controls preload="metadata" src={url}></video>
			</div>
		{:else}
			<div class="center-note">{$t('preview.unsupported')}</div>
		{/if}
	</div>
</Modal>

<style>
	@reference "../../../app.css";

	:global(.upload-preview) {
		overflow: hidden !important;
	}

	.preview-stage {
		flex: 1;
		min-height: 0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		background: var(--app-bg);
	}

	.fill {
		flex: 1;
		min-height: 0;
		height: 100%;
		overflow: hidden;
	}

	.fill-frame {
		flex: 1;
		min-height: 0;
		width: 100%;
		height: 100%;
		border: 0;
		background: white;
	}

	.scroll-preview {
		flex: 1;
		min-height: 0;
		overflow: auto;
		padding: 0.875rem 1rem;
	}

	.mono {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		line-height: 1.45;
		color: var(--app-fg);
	}

	.mono pre {
		margin: 0;
		white-space: pre-wrap;
		word-break: break-word;
	}

	.truncate-note {
		margin: 0 0 0.75rem;
		font-family: inherit;
		font-size: 0.75rem;
		color: var(--app-fg-muted);
	}

	.center-note {
		flex: 1;
		min-height: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 1.5rem;
		text-align: center;
		font-size: 0.875rem;
		color: var(--app-fg-muted);
	}

	.center-note audio,
	.center-note video {
		width: min(100%, 40rem);
		max-height: 100%;
	}

	.video-note {
		background: black;
	}
</style>
