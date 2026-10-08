/**
 * Shared Socket.IO connection: singleton managed by layout.
 *
 * Any store (chat, future features) registers listeners on this.
 * Layout calls connect() on mount, disconnect() on destroy.
 */

import { io, type Socket } from 'socket.io-client';

let socket: Socket | null = null;
let _connected = $state(false);
type SocketHandler = (...args: any[]) => void;
const registeredListeners = new Map<string, Set<SocketHandler>>();

function bindRegisteredListeners(currentSocket: Socket) {
	for (const [event, handlers] of registeredListeners) {
		for (const handler of handlers) {
			currentSocket.on(event, handler);
		}
	}
}

function connect() {
	if (socket) return;
	socket = io({
		reconnection: true,
		reconnectionDelay: 1000,
		withCredentials: true,
		transports: ['websocket', 'polling'],
		tryAllTransports: true
	});

	socket.on('connect', () => {
		_connected = true;
	});

	socket.on('disconnect', (reason) => {
		_connected = false;
		// The server drops sockets when it signs a user out of other sessions (password or
		// two-step sign-in changed). Socket.IO won't retry that on its own; this browser's
		// fresh cookie gets it back in, while a signed-out device is refused.
		if (reason === 'io server disconnect') setTimeout(() => socket?.connect(), 1000);
	});

	bindRegisteredListeners(socket);
}

function disconnect() {
	const currentSocket = socket;
	socket = null;
	currentSocket?.disconnect();
	_connected = false;
}

/** Get the raw socket instance for registering listeners. */
function getSocket(): Socket | null {
	return socket;
}

function on(event: string, handler: SocketHandler): () => void {
	let handlers = registeredListeners.get(event);
	if (!handlers) {
		handlers = new Set();
		registeredListeners.set(event, handlers);
	}
	const wasRegistered = handlers.has(handler);
	handlers.add(handler);
	if (socket && !wasRegistered) {
		socket.on(event, handler);
	}
	return () => off(event, handler);
}

function off(event: string, handler: SocketHandler) {
	const handlers = registeredListeners.get(event);
	handlers?.delete(handler);
	if (handlers?.size === 0) {
		registeredListeners.delete(event);
	}
	socket?.off(event, handler);
}

export const socketStore = {
	connect,
	disconnect,
	getSocket,
	on,
	off,
	get connected() {
		return _connected;
	}
};
