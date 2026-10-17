export interface DesktopHaruSnapshot {
  version: number;
  theme?: 'light' | 'dark';
  conversationId: number | null;
  taskState: 'idle' | 'running' | 'waiting_confirmation' | 'completed' | 'failed';
  messages: { role: 'user' | 'assistant'; content: string }[];
  contextLabel: string;
  loading: boolean;
  hasPending: boolean;
  animationLevel?: 'full' | 'minimal' | 'off';
  aiConfigured?: boolean;
  canSend: boolean;
  canStop: boolean;
  stopping: boolean;
  error: string;
  stopMessage: string;
}
export interface DesktopHaruState {
  connected: boolean;
  generation: number;
  snapshot: DesktopHaruSnapshot | null;
  visible: boolean;
  expanded: boolean;
  alwaysOnTop: boolean;
}
export type DesktopHaruResult = { ok: true } | { ok: false; reason: 'unavailable' | 'busy' | 'stale' | 'uncertain' };
export type DesktopHaruRequest = { action: 'send' | 'stop' | 'open-pending'; version: number; generation: number; text?: string };
export type DesktopHaruCommand = Omit<DesktopHaruRequest, 'generation'> & { id: number };
export type DesktopHaruWindowAction = 'show-main' | 'show-ai-settings' | 'show-haru' | 'hide-haru' | 'context-menu' | 'toggle-top' | 'expand' | 'collapse';
interface DesktopHaruBridge {
  role: 'owner' | 'haru';
  getState(): Promise<DesktopHaruState | null>;
  onState(listener: (state: DesktopHaruState) => void): () => void;
  windowAction(action: DesktopHaruWindowAction): Promise<boolean>;
  dragWindow?(phase: 'start' | 'move' | 'end' | 'cancel'): Promise<boolean>;
  onShowAISettings?(listener: () => void): () => void;
  onShowPilot?(listener: () => void): () => void;
  publish?(snapshot: DesktopHaruSnapshot): void;
  disconnect?(): void;
  onCommand?(listener: (command: DesktopHaruCommand) => void): () => void;
  reply?(id: number, result: DesktopHaruResult): void;
  request?(request: DesktopHaruRequest): Promise<DesktopHaruResult>;
}
declare global {
  interface Window { offerpilotDesktop?: DesktopHaruBridge }
}
export const isDesktopOwner = () => window.offerpilotDesktop?.role === 'owner';
export const isDesktopHaru = () => window.offerpilotDesktop?.role === 'haru';
