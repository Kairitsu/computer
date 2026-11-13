<script lang="ts">
	import { t } from '$lib/i18n';

	interface Props {
		content: string;
	}

	let { content }: Props = $props();
</script>

<div class="html-preview">
	<iframe
		srcdoc={content}
		title={$t('preview.htmlTitle')}
		sandbox=""
		class="preview-iframe"
	></iframe>
</div>

<style>
	@reference "../../../app.css";

	.html-preview {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow: hidden;
	}

	.preview-iframe {
		width: 100%;
		height: 100%;
		border: none;
		background: white;
	}
</style>
