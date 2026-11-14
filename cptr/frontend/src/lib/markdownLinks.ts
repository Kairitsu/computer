/** Which markdown URLs are safe to put in a real href or img src. */

const HAS_CONTROL = /[\u0000-\u0020]/;

export function linkHref(value: string | null | undefined): string | null {
	if (!value) return null;
	const href = value.trim();
	if (!href || HAS_CONTROL.test(href)) return null;
	if (/^https?:\/\//i.test(href) || /^mailto:/i.test(href)) return href;
	return null;
}

export function imageSrc(value: string | null | undefined): string | null {
	if (!value) return null;
	const src = value.trim();
	if (!src || HAS_CONTROL.test(src) || src.startsWith('//') || src.startsWith('\\')) return null;
	if (/^https?:\/\//i.test(src)) return src;
	// javascript:, data:, and any other scheme stay text.
	if (/^[a-z][a-z0-9+.-]*:/i.test(src)) return null;
	return src;
}
