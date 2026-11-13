<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchHandler } from '$lib/apis';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		src: string;
		fileName: string;
	}

	let { src, fileName }: Props = $props();

	let loading = $state(true);
	let error = $state('');
	let htmlContent = $state('');
	let sheetNames = $state<string[]>([]);
	let activeSheet = $state(0);
	let sheets = $state<{ name: string; rows: string[][] }[]>([]);
	let sheetTruncated = $state(false);
	let slideImages = $state<string[]>([]);

	type SheetPayload = { name?: string; rows?: unknown[][] };

	let activeRows = $derived(sheets[activeSheet]?.rows ?? []);
	let colCount = $derived(activeRows.reduce((max, row) => Math.max(max, row.length), 0));
	let columns = $derived(Array.from({ length: colCount }, (_, index) => index));

	let ext = $derived(fileName.split('.').pop()?.toLowerCase() ?? '');
	let isDocx = $derived(ext === 'docx');
	let isXlsx = $derived(ext === 'xlsx' || ext === 'xls');
	let isPptx = $derived(ext === 'pptx');

	function sheetsUrl(fileUrl: string): string {
		const [path, query] = fileUrl.split('?');
		const next = `${path}/sheets`;
		return query ? `${next}?${query}` : next;
	}

	function cellText(value: unknown): string {
		if (value === null || value === undefined) return '';
		return String(value);
	}

	function isNumberText(value: string): boolean {
		return /^-?\d+(\.\d+)?$/.test(value);
	}

	function colLetter(index: number): string {
		let s = '';
		let n = index;
		while (n >= 0) {
			s = String.fromCharCode(65 + (n % 26)) + s;
			n = Math.floor(n / 26) - 1;
		}
		return s;
	}

	async function loadFile() {
		loading = true;
		error = '';
		try {
			if (isXlsx) {
				await loadXlsx();
				return;
			}
			const res = await fetchHandler(src);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const buf = await res.arrayBuffer();

			if (isDocx) {
				await loadDocx(buf);
			} else if (isPptx) {
				await loadPptx(buf);
			}
		} catch (e: any) {
			console.error('Office load error:', e);
			error = e.message || 'Failed to load file.';
		} finally {
			loading = false;
		}
	}

	async function loadDocx(buf: ArrayBuffer) {
		const mammoth = await import('mammoth');
		const result = await mammoth.convertToHtml({ arrayBuffer: buf });
		const DOMPurify = (await import('dompurify')).default;
		htmlContent = DOMPurify.sanitize(result.value);
	}

	async function loadXlsx() {
		const res = await fetchHandler(sheetsUrl(src));
		if (!res.ok) throw new Error(`HTTP ${res.status}`);
		const body = (await res.json()) as { sheets?: SheetPayload[]; truncated?: boolean };
		const parsed = (body.sheets ?? []).map((sheet) => ({
			name: sheet.name || 'Sheet',
			rows: (sheet.rows ?? []).map((row) => (Array.isArray(row) ? row.map(cellText) : []))
		}));
		sheets = parsed;
		sheetNames = parsed.map((sheet) => sheet.name);
		sheetTruncated = Boolean(body.truncated);
		activeSheet = 0;
	}

	async function loadPptx(buf: ArrayBuffer) {
		const JSZip = (await import('jszip')).default;
		const zip = await JSZip.loadAsync(buf);

		// Slide dimensions
		let slideW = 960,
			slideH = 540;
		const presXml = zip.file('ppt/presentation.xml');
		if (presXml) {
			const presText = await presXml.async('text');
			const doc = new DOMParser().parseFromString(presText, 'application/xml');
			const sldSz = doc.getElementsByTagName('p:sldSz')[0];
			if (sldSz) {
				const EMU = 9525;
				slideW = Math.round((parseInt(sldSz.getAttribute('cx') ?? '0') || 0) / EMU);
				slideH = Math.round((parseInt(sldSz.getAttribute('cy') ?? '0') || 0) / EMU);
			}
		}

		// Collect media
		const media: Record<string, string> = {};
		const mediaFiles = Object.keys(zip.files).filter((f) => f.startsWith('ppt/media/'));
		await Promise.all(
			mediaFiles.map(async (path) => {
				const file = zip.file(path);
				if (!file) return;
				const b64 = await file.async('base64');
				const ext = path.split('.').pop()?.toLowerCase() ?? '';
				const mime = ext === 'png' ? 'image/png' : ext === 'gif' ? 'image/gif' : 'image/jpeg';
				media[path] = `data:${mime};base64,${b64}`;
			})
		);

		// Discover slides
		const slideFiles = Object.keys(zip.files)
			.filter((f) => /^ppt\/slides\/slide\d+\.xml$/.test(f))
			.sort(
				(a, b) =>
					parseInt(a.match(/slide(\d+)/)?.[1] ?? '0') - parseInt(b.match(/slide(\d+)/)?.[1] ?? '0')
			);

		const images: string[] = [];

		for (const slidePath of slideFiles) {
			const slideText = await zip.file(slidePath)!.async('text');
			const slideDoc = new DOMParser().parseFromString(slideText, 'application/xml');

			const slideNum = slidePath.match(/slide(\d+)/)?.[1];
			const relsPath = `ppt/slides/_rels/slide${slideNum}.xml.rels`;
			const rels: Record<string, string> = {};
			const relsFile = zip.file(relsPath);
			if (relsFile) {
				const relsDoc = new DOMParser().parseFromString(
					await relsFile.async('text'),
					'application/xml'
				);
				const relEls = relsDoc.getElementsByTagName('Relationship');
				for (let i = 0; i < relEls.length; i++) {
					const id = relEls[i].getAttribute('Id') ?? '';
					const target = relEls[i].getAttribute('Target') ?? '';
					rels[id] = target.startsWith('../') ? 'ppt/' + target.replace('../', '') : target;
				}
			}

			const canvas = document.createElement('canvas');
			canvas.width = slideW;
			canvas.height = slideH;
			const ctx = canvas.getContext('2d')!;
			ctx.fillStyle = '#ffffff';
			ctx.fillRect(0, 0, slideW, slideH);

			const spTree = slideDoc.getElementsByTagName('p:spTree')[0];
			if (spTree) {
				const shapes = [
					...Array.from(spTree.getElementsByTagName('p:sp')),
					...Array.from(spTree.getElementsByTagName('p:pic'))
				];

				for (const shape of shapes) {
					const xfrm = shape.getElementsByTagName('a:xfrm')[0];
					if (!xfrm) continue;
					const off = xfrm.getElementsByTagName('a:off')[0];
					const ext = xfrm.getElementsByTagName('a:ext')[0];
					if (!off || !ext) continue;

					const EMU = 9525;
					const x = Math.round(parseInt(off.getAttribute('x') ?? '0') / EMU);
					const y = Math.round(parseInt(off.getAttribute('y') ?? '0') / EMU);
					const w = Math.round(parseInt(ext.getAttribute('cx') ?? '0') / EMU);
					const h = Math.round(parseInt(ext.getAttribute('cy') ?? '0') / EMU);
					if (w === 0 && h === 0) continue;

					// Picture
					const blipFill = shape.getElementsByTagName('p:blipFill')[0];
					if (blipFill) {
						const blip = blipFill.getElementsByTagName('a:blip')[0];
						if (blip) {
							const rEmbed = blip.getAttribute('r:embed') ?? '';
							const dataUri = media[rels[rEmbed]] ?? '';
							if (dataUri) {
								try {
									const img = await loadImage(dataUri);
									ctx.drawImage(img, x, y, w, h);
								} catch {}
							}
						}
						continue;
					}

					// Text
					const txBody = shape.getElementsByTagName('p:txBody')[0];
					if (!txBody) continue;

					ctx.save();
					ctx.rect(x, y, w, h);
					ctx.clip();

					const paragraphs = txBody.getElementsByTagName('a:p');
					let cursorY = y;

					for (let pi = 0; pi < paragraphs.length; pi++) {
						const runs = paragraphs[pi].getElementsByTagName('a:r');
						if (runs.length === 0) {
							cursorY += 18;
							continue;
						}

						let maxPt = 12;
						for (let ri = 0; ri < runs.length; ri++) {
							const rPr = runs[ri].getElementsByTagName('a:rPr')[0];
							const sz = rPr?.getAttribute('sz');
							if (sz) maxPt = Math.max(maxPt, parseInt(sz) / 100);
						}

						cursorY += maxPt;
						let cursorX = x + 4;

						for (let ri = 0; ri < runs.length; ri++) {
							const run = runs[ri];
							const rPr = run.getElementsByTagName('a:rPr')[0];
							const text = run.getElementsByTagName('a:t')[0]?.textContent ?? '';
							if (!text) continue;

							let fontPt = 12,
								bold = false,
								italic = false,
								color = '#000000';
							if (rPr) {
								if (rPr.getAttribute('b') === '1') bold = true;
								if (rPr.getAttribute('i') === '1') italic = true;
								const sz = rPr.getAttribute('sz');
								if (sz) fontPt = parseInt(sz) / 100;
								const srgb = rPr.getElementsByTagName('a:srgbClr')[0];
								if (srgb?.getAttribute('val')) color = `#${srgb.getAttribute('val')}`;
							}

							ctx.font = `${italic ? 'italic ' : ''}${bold ? 'bold ' : ''}${fontPt}pt Calibri, Arial, sans-serif`;
							ctx.fillStyle = color;
							ctx.textBaseline = 'alphabetic';

							const words = text.split(/(\s+)/);
							for (const word of words) {
								const m = ctx.measureText(word);
								if (cursorX + m.width > x + w && cursorX > x + 4) {
									cursorX = x + 4;
									cursorY += maxPt * 1.4;
								}
								if (cursorY > y + h) break;
								ctx.fillText(word, cursorX, cursorY);
								cursorX += m.width;
							}
						}
						cursorY += maxPt * 0.6;
					}
					ctx.restore();
				}
			}

			images.push(canvas.toDataURL('image/png'));
		}

		slideImages = images;
	}

	function loadImage(src: string): Promise<HTMLImageElement> {
		return new Promise((resolve, reject) => {
			const img = new Image();
			img.onload = () => resolve(img);
			img.onerror = () => reject(new Error('Failed to load image'));
			img.src = src;
		});
	}

	onMount(() => {
		loadFile();
	});
</script>

<div class="office-view">
	{#if loading}
		<div class="state"><Spinner size={20} /></div>
	{:else if error}
		<div class="state error-msg">{error}</div>
	{:else if isDocx}
		<div class="docx-content">{@html htmlContent}</div>
	{:else if isXlsx}
		{#if sheetNames.length > 1}
			<div class="sheet-tabs">
				{#each sheetNames as name, i}
					<button
						class="sheet-tab"
						class:active={activeSheet === i}
						onclick={() => {
							activeSheet = i;
						}}>{name}</button
					>
				{/each}
			</div>
		{/if}
		<div class="xlsx-content">
			{#if sheetTruncated}<p class="truncate-note">{$t('preview.truncated')}</p>{/if}
			{#if activeRows.length === 0}
				<p>{$t('preview.emptySheet')}</p>
			{:else}
				<table>
					<thead>
						<tr>
							<th class="rn"></th>
							{#each columns as c}
								<th>{colLetter(c)}</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each activeRows as row, r}
							<tr>
								<td class="rn">{r + 1}</td>
								{#each columns as c}
									{@const value = row[c] ?? ''}
									<td class:num={isNumberText(value)}>{value === '' ? '\u00a0' : value}</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
	{:else if isPptx}
		<div class="pptx-content">
			{#each slideImages as img, i}
				<div class="slide">
					<span class="slide-num">{i + 1}</span>
					<img src={img} alt="Slide {i + 1}" />
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	@reference "../../../app.css";

	.office-view {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow: hidden;
	}

	.state {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100%;
	}

	.error-msg {
		font-size: 0.8125rem;
		color: #ef4444;
	}

	/* ── DOCX ────────────────────────────────────── */

	.docx-content {
		flex: 1;
		overflow-y: auto;
		padding: 1.5rem 2rem;
		font-size: 0.875rem;
		line-height: 1.7;
		color: var(--app-fg);
	}

	.docx-content :global(h1) {
		font-size: 1.5rem;
		font-weight: 600;
		margin: 0 0 0.5rem;
	}
	.docx-content :global(h2) {
		font-size: 1.25rem;
		font-weight: 600;
		margin: 1.5rem 0 0.5rem;
	}
	.docx-content :global(h3) {
		font-size: 1rem;
		font-weight: 600;
		margin: 1.25rem 0 0.375rem;
	}
	.docx-content :global(p) {
		margin: 0 0 0.75rem;
	}
	.docx-content :global(table) {
		border-collapse: collapse;
		margin: 0.75rem 0;
		font-size: 0.8125rem;
	}
	.docx-content :global(td),
	.docx-content :global(th) {
		border: 1px solid var(--app-border);
		padding: 0.25rem 0.5rem;
	}
	.docx-content :global(img) {
		max-width: 100%;
		height: auto;
	}

	/* ── XLSX ────────────────────────────────────── */

	.sheet-tabs {
		display: flex;
		gap: 0;
		border-bottom: 1px solid var(--app-border);
		padding: 0 0.25rem;
		flex-shrink: 0;
		overflow-x: auto;
	}

	.sheet-tab {
		padding: 0.375rem 0.75rem;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--app-fg-muted);
		border-bottom: 0.125rem solid transparent;
		white-space: nowrap;
		transition: all 0.1s;
	}

	.sheet-tab.active {
		color: var(--app-fg);
		border-bottom-color: var(--app-fg);
	}

	.xlsx-content {
		flex: 1;
		overflow: auto;
	}

	.xlsx-content :global(table) {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.75rem;
		font-family: var(--font-mono);
		white-space: nowrap;
	}

	.xlsx-content :global(thead) {
		position: sticky;
		top: 0;
		z-index: 2;
	}

	.xlsx-content :global(th) {
		background: var(--app-hover);
		color: var(--app-fg-muted);
		font-weight: 600;
		font-size: 0.625rem;
		padding: 0.25rem 0.5rem;
		text-align: center;
		border-bottom: 1px solid var(--app-border);
	}

	.xlsx-content :global(td) {
		padding: 0.1875rem 0.5rem;
		color: var(--app-fg);
		border-bottom: 1px solid var(--app-divider);
		border-right: 1px solid var(--app-divider);
	}

	.xlsx-content :global(.rn) {
		color: var(--app-fg-subtle);
		font-size: 0.625rem;
		text-align: right;
		user-select: none;
		min-width: 1.75rem;
		border-right: 1px solid var(--app-border);
		background: var(--app-hover);
	}

	.xlsx-content :global(.num) {
		text-align: right;
		color: #d97706;
	}
	:global(.dark) .xlsx-content :global(.num) {
		color: #fbbf24;
	}

	/* ── PPTX ────────────────────────────────────── */

	.pptx-content {
		flex: 1;
		overflow-y: auto;
		padding: 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		align-items: center;
		background: var(--app-hover);
	}

	.slide {
		position: relative;
		width: 100%;
		max-width: 50rem;
		box-shadow: 0 0.0625rem 0.25rem color-mix(in oklab, var(--app-fg) 12%, transparent);
		border-radius: 0.25rem;
		overflow: hidden;
	}

	.slide img {
		width: 100%;
		display: block;
	}

	.slide-num {
		position: absolute;
		top: 0.375rem;
		left: 0.375rem;
		font-size: 0.625rem;
		font-weight: 600;
		color: white;
		background: rgba(0, 0, 0, 0.4);
		padding: 0.0625rem 0.375rem;
		border-radius: 0.1875rem;
	}
</style>
