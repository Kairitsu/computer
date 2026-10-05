/**
 * System events store.
 *
 * WebSocket to /api/events/ws that carries fs_change events (for FileBrowser
 * auto-refresh).
 *
 * Replaces the old fsWatch store.
 */

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let currentWatchPath: string | null = null;

// ── FS events ────────────────────────────────────────────────────
let _fsTick = $state(0);
let _fsChangedPaths = $state<string[]>([]);

function connect(watchPath: string) {
	currentWatchPath = watchPath;

	if (ws && ws.readyState <= WebSocket.OPEN) {
		// Already connected, just update watch path
		if (ws.readyState === WebSocket.OPEN) {
			ws.send(JSON.stringify({ type: 'watch_path', path: watchPath }));
		}
		return;
	}

	const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
	const url = `${protocol}//${location.host}/api/events/ws?path=${encodeURIComponent(watchPath)}`;

	ws = new WebSocket(url);

	ws.onmessage = (event) => {
		try {
			const msg = JSON.parse(event.data);

			if (msg.type === 'fs_change') {
				_fsChangedPaths = msg.paths ?? [];
				_fsTick++;
			}
		} catch {}
	};

	ws.onclose = () => {
		ws = null;
		if (reconnectTimer) clearTimeout(reconnectTimer);
		reconnectTimer = setTimeout(() => {
			if (currentWatchPath) connect(currentWatchPath);
		}, 2000);
	};

	ws.onerror = () => {
		ws?.close();
	};
}

function disconnect() {
	if (reconnectTimer) {
		clearTimeout(reconnectTimer);
		reconnectTimer = null;
	}
	if (ws) {
		ws.onclose = null;
		ws.close();
		ws = null;
	}
	currentWatchPath = null;
}

function watchPath(path: string) {
	currentWatchPath = path;
	if (ws && ws.readyState === WebSocket.OPEN) {
		ws.send(JSON.stringify({ type: 'watch_path', path }));
	}
}

function isRelevantFsChange(targetPath: string): boolean {
	return _fsChangedPaths.some((p) => p === targetPath || p.startsWith(targetPath + '/'));
}

export const systemEvents = {
	connect,
	disconnect,
	watchPath,

	// FS
	get fsTick() {
		return _fsTick;
	},
	get fsChangedPaths() {
		return _fsChangedPaths;
	},
	isRelevantFsChange
};
