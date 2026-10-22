/**
 * Auth API: login, setup, session, logout.
 */
import { ApiError, fetchHandler, fetchJSON, jsonBody } from '$lib/apis';

interface SessionResponse {
	authenticated: boolean;
	user_id?: string;
	username?: string;
	display_name?: string | null;
	role?: string;
	profile_image_url?: string | null;
	exp?: number;
}

interface ConfigResponse {
	auth_mode: string;
	needs_setup: boolean;
	signup_enabled: boolean;
	version: string;
}

export const getSession = () => fetchJSON<SessionResponse>('/api/auth');

export const getConfig = () => fetchJSON<ConfigResponse>('/api/config');

/**
 * Sign in. Resolves `{ totp_required: true }` when the password is right but the account
 * also needs a code from its authenticator app (pass it as `code` on the next call).
 */
export const login = async (
	username: string,
	password: string,
	code?: string
): Promise<{ ok?: boolean; totp_required?: boolean }> => {
	const res = await fetchHandler('/api/auth/login', jsonBody({ username, password, code }));
	const data = await res.json().catch(() => ({}));
	if (res.ok) return data;
	if (data.totp_required && !code) return { totp_required: true };
	throw new ApiError(res.status, data.error || res.statusText);
};

export const setup = (username: string, password: string, token: string) =>
	fetchJSON('/api/auth/setup', jsonBody({ username, password, token }));

export const signup = (username: string, password: string) =>
	fetchJSON<{ ok?: boolean; pending?: boolean }>(
		'/api/auth/signup',
		jsonBody({ username, password })
	);

export const logout = () => fetchHandler('/api/auth/logout', { method: 'POST' }).catch(() => {});

/** Check the current password. Returns a short-lived grant for setting a new one. */
export const verifyCurrentPassword = (currentPassword: string) =>
	fetchJSON<{ grant: string }>(
		'/api/auth/password/verify',
		jsonBody({ current_password: currentPassword })
	);

/** Check an authenticator code or a recovery code. Returns the same kind of grant. */
export const verifyPasswordCode = (method: 'totp' | 'recovery', code: string) =>
	fetchJSON<{ grant: string }>('/api/auth/password/verify-code', jsonBody({ method, code }));

/** Set a new password with a grant, then the server signs this browser out. */
export const updatePassword = (grant: string, newPassword: string) =>
	fetchJSON('/api/auth/password', jsonBody({ grant, new_password: newPassword }));

/** Upload avatar (resized client-side). Returns { ok, profile_image_url }. */
export const uploadAvatar = async (
	blob: Blob
): Promise<{ ok: boolean; profile_image_url: string }> => {
	const form = new FormData();
	form.append('file', blob, 'avatar.png');
	return fetchJSON('/api/auth/avatar', { method: 'PUT', body: form });
};

/** Delete avatar. */
export const deleteAvatar = () => fetchJSON('/api/auth/avatar', { method: 'DELETE' });

/** Update display name. */
export const updateProfile = (display_name: string | null) =>
	fetchJSON('/api/auth/profile', { ...jsonBody({ display_name }), method: 'PUT' });

/** Change the login username. The server keeps this browser signed in. */
export const updateUsername = (username: string) =>
	fetchJSON<{ ok: boolean; username: string }>('/api/auth/username', {
		...jsonBody({ username }),
		method: 'PUT'
	});

export interface TotpStatus {
	enabled: boolean;
	recovery_codes_left: number;
}

export interface TotpSetup {
	secret: string;
	otpauth_uri: string;
	qr_svg: string;
}

/** Two-step sign-in status for the signed-in user. */
export const getTotpStatus = () => fetchJSON<TotpStatus>('/api/auth/totp');

/** Start two-step sign-in setup (needs the password). Returns the secret and its QR code. */
export const setupTotp = (password: string) =>
	fetchJSON<TotpSetup>('/api/auth/totp/setup', jsonBody({ password }));

/** Confirm setup with a code from the app. Returns the recovery codes (shown once). */
export const enableTotp = (code: string) =>
	fetchJSON<{ ok: boolean; recovery_codes: string[] }>('/api/auth/totp/enable', jsonBody({ code }));

/** Turn two-step sign-in off (password plus an authenticator or recovery code). */
export const disableTotp = (password: string, code: string) =>
	fetchJSON('/api/auth/totp/disable', jsonBody({ password, code }));
