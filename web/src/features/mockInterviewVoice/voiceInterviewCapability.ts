export type LocalSpeechLanguageState = 'available' | 'downloadable' | 'downloading' | 'unavailable';

export interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0: { transcript: string };
  }>;
}

export interface SpeechRecognitionLike {
  processLocally: boolean;
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

export interface SpeechRecognitionConstructorLike {
  new(): SpeechRecognitionLike;
  available?: (options: { langs: string[]; processLocally: true }) => Promise<unknown>;
  install?: (options: { langs: string[] }) => Promise<boolean>;
}

export interface VoiceInterviewBrowser {
  MediaRecorder?: unknown;
  speechSynthesis?: { speak?: unknown };
  SpeechRecognition?: SpeechRecognitionConstructorLike;
}

export interface VoiceInterviewCapabilities {
  recorder: boolean;
  speechSynthesis: boolean;
  localRecognition: boolean;
}

export function detectVoiceInterviewCapabilities(browser: VoiceInterviewBrowser): VoiceInterviewCapabilities {
  let localRecognition = false;
  if (browser.SpeechRecognition) {
    try {
      localRecognition = typeof new browser.SpeechRecognition().processLocally === 'boolean';
    } catch {
      localRecognition = false;
    }
  }
  return {
    recorder: typeof browser.MediaRecorder === 'function',
    speechSynthesis: typeof browser.speechSynthesis?.speak === 'function',
    localRecognition,
  };
}

export async function queryLocalSpeechLanguage(
  SpeechRecognition: SpeechRecognitionConstructorLike | undefined,
  lang: string,
): Promise<LocalSpeechLanguageState> {
  if (!SpeechRecognition?.available) return 'unavailable';
  try {
    const state = await SpeechRecognition.available({ langs: [lang], processLocally: true });
    return state === 'available' || state === 'downloadable' || state === 'downloading'
      ? state
      : 'unavailable';
  } catch {
    return 'unavailable';
  }
}

export async function ensureLocalSpeechLanguage(
  SpeechRecognition: SpeechRecognitionConstructorLike | undefined,
  lang: string,
): Promise<boolean> {
  if (!SpeechRecognition?.install) return false;
  try {
    return await SpeechRecognition.install({ langs: [lang] });
  } catch {
    return false;
  }
}

export function createLocalSpeechRecognition(
  SpeechRecognition: SpeechRecognitionConstructorLike,
  lang: string,
): SpeechRecognitionLike {
  const recognition = new SpeechRecognition();
  recognition.processLocally = true;
  recognition.lang = lang;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onresult = null;
  recognition.onerror = null;
  recognition.onend = null;
  return recognition;
}
