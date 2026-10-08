export type UpdateStatus =
  | 'unavailable'
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'installing'
  | 'error';

export interface UpdateState {
  status: UpdateStatus;
  currentVersion: string;
  reason?: string;
  version?: string;
  releaseNotes?: string;
  percent?: number;
}

export interface UpdateSafetySnapshot {
  ready: boolean;
  hasDraft: boolean;
  activeRun: boolean;
  pendingApproval: boolean;
  reason?: string;
}

export interface PrepareInstallRequest {
  id: string;
}

/** Exposed by the desktop preload to the owner window only, never Haru/web. */
export interface DesktopUpdatesBridge {
  getState(): Promise<UpdateState>;
  check(): Promise<UpdateState>;
  download(): Promise<UpdateState>;
  install(): Promise<UpdateState>;
  onState(listener: (state: UpdateState) => void): () => void;
  onPrepareInstall(listener: (request: PrepareInstallRequest) => void): () => void;
  replyPrepareInstall(id: string, snapshot: UpdateSafetySnapshot): void;
}

declare global {
  interface Window {
    offerpilotUpdates?: DesktopUpdatesBridge;
  }
}
