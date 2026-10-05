export type LaunchIntentKind =
	| 'newNote'
	| 'newChat'
	| 'openWorkspace'
	| 'search'
	| 'openChat'
	| 'openFile'
	| 'openDir'
	| 'share'
	| 'importFiles';

export type ShareBehavior = 'ask' | 'chatDraft' | 'noteFile';
export type ImportDestination = 'workspaceRoot' | 'askFolder' | 'configuredFolder';

export interface PwaPreferences {
	shareBehavior: ShareBehavior;
	importDestination: ImportDestination;
	importFolder?: string;
}

export interface ShareFilePayload {
	name: string;
	type: string;
	lastModified?: number;
	file: File;
}

export interface SharePayload {
	id?: string;
	title?: string;
	text?: string;
	url?: string;
	files?: ShareFilePayload[];
}

export interface FileImportPayload {
	files: File[];
}

export interface LaunchIntent {
	kind: LaunchIntentKind;
	workspace?: string;
	chatId?: string | null;
	filePath?: string;
	dirPath?: string;
	targetDir?: string;
	payloadId?: string;
	shareBehavior?: ShareBehavior;
	share?: SharePayload;
	importFiles?: FileImportPayload;
}

export const defaultPwaPreferences: PwaPreferences = {
	shareBehavior: 'ask',
	importDestination: 'workspaceRoot'
};
