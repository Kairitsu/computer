/**
 * Number and time formatting for Settings → Usage, after Grok App:
 * compact counts use the locale's own units (1.7亿 / 3112.7万 / 31.1M).
 */

const TIGHT_SCRIPT = /^(zh|ja|ko)\b/i;

/** CJK locales join numbers and units without spaces. */
export function isTightScript(locale: string): boolean {
	return TIGHT_SCRIPT.test(locale);
}

function safeLocale(locale: string): string | undefined {
	try {
		return Intl.getCanonicalLocales(locale)[0];
	} catch {
		return undefined;
	}
}

export function formatCount(value: number | null | undefined, locale: string): string {
	if (value == null || !Number.isFinite(value)) return '—';
	return new Intl.NumberFormat(safeLocale(locale), {
		notation: 'compact',
		maximumFractionDigits: 1
	}).format(value);
}

function unit(value: number, name: 'hour' | 'minute' | 'second' | 'day', locale: string) {
	return new Intl.NumberFormat(safeLocale(locale), {
		style: 'unit',
		unit: name,
		// "long" reads 秒钟 in zh-CN; "short" gives 56分钟47秒 / 56 min 47 sec.
		unitDisplay: 'short'
	}).format(value);
}

/** 3384 → "56分钟24秒" / "56 min 24 sec"; at most two units. */
export function formatStatDuration(seconds: number | null | undefined, locale: string): string {
	if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return '—';
	const total = Math.floor(seconds);
	const h = Math.floor(total / 3600);
	const m = Math.floor((total % 3600) / 60);
	const s = total % 60;
	const sep = isTightScript(locale) ? '' : ' ';
	if (h > 0)
		return m > 0
			? `${unit(h, 'hour', locale)}${sep}${unit(m, 'minute', locale)}`
			: unit(h, 'hour', locale);
	if (m > 0)
		return s > 0
			? `${unit(m, 'minute', locale)}${sep}${unit(s, 'second', locale)}`
			: unit(m, 'minute', locale);
	return unit(s, 'second', locale);
}

export function formatDays(days: number, locale: string): string {
	return unit(days, 'day', locale);
}

/** Unix seconds → "10/10 16:32" in the locale's order. */
export function formatResetTime(seconds: number | null | undefined, locale: string): string {
	if (!seconds) return '';
	return new Intl.DateTimeFormat(safeLocale(locale), {
		month: '2-digit',
		day: '2-digit',
		hour: '2-digit',
		minute: '2-digit'
	}).format(new Date(seconds * 1000));
}
