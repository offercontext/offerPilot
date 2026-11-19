import {
  automaticAcceptance, createVoiceSession, voiceOffer, voiceSessionAction,
  type VoiceGrant,
} from '@/services/interviewVoice';
import type { InterviewStudioContext } from '@/services/mockInterviews';
import type { AnswerAcceptance } from '@/types/mockInterview';

export interface RealtimeState {
  status: 'idle' | 'connecting' | 'listening' | 'waiting' | 'submitting' | 'paused' | 'manual';
  locked: boolean;
  transcript: string;
  supplemental: boolean;
  message: string;
  revocationPending?: boolean;
}
export interface RealtimeAnswer {
  text: string; turnNo: number; turnKey: string; acceptance: AnswerAcceptance;
}
export interface MediaConnection { send(data: object): void; mute(): void; close(): void }
interface Dependencies {
  create: typeof createVoiceSession;
  action: typeof voiceSessionAction;
  connect: typeof connectMedia;
  key: () => string;
}
interface Options {
  onState: (state: RealtimeState) => void;
  onAnswer: (answer: RealtimeAnswer) => Promise<'saved' | 'unknown' | 'failed'>;
}
export const initialRealtimeState: RealtimeState = {
  status: 'idle', locked: false, transcript: '', supplemental: false, message: '',
};

export class RealtimeVoiceController {
  state = { ...initialRealtimeState };
  private generation = 0;
  private grant?: VoiceGrant;
  private media?: MediaConnection;
  private abort?: AbortController;
  private heartbeat?: ReturnType<typeof setInterval>;
  private turnNo = 0;
  private sealedTurn = 0;
  private lastRevision = -1;
  private businessPending = false;
  private deps: Dependencies;

  constructor(private options: Options, deps: Partial<Dependencies> = {}) {
    this.deps = {
      create: createVoiceSession, action: voiceSessionAction, connect: connectMedia,
      key: () => crypto.randomUUID(), ...deps,
    };
  }
  private patch(data: Partial<RealtimeState>) {
    this.state = { ...this.state, ...data };
    this.options.onState(this.state);
  }
  async start(input: {
    context: InterviewStudioContext; attemptId: number; turnNo: number;
    consentRevision: string; takeover: boolean;
  }) {
    if (this.state.locked || this.businessPending || this.state.transcript.trim()) return;
    const generation = ++this.generation;
    this.turnNo = input.turnNo;
    this.sealedTurn = 0;
    this.lastRevision = -1;
    this.patch({ status: 'connecting', locked: true, message: '', transcript: '', supplemental: false, revocationPending: false });
    try {
      const grant = await this.deps.create({ ...input, requestKey: this.deps.key() });
      if (generation !== this.generation) {
        await this.deps.action(grant, 'close');
        return;
      }
      this.grant = grant;
      this.abort = new AbortController();
      this.heartbeat = setInterval(() => {
        void this.deps.action(grant, 'heartbeat').catch(() => {
          if (generation === this.generation) void this.pause('语音授权已失效，请恢复后重新开启。');
        });
      }, 10000);
      const media = await this.deps.connect(grant,
        (data) => { if (generation === this.generation) void this.receive(data); },
        () => { if (generation === this.generation) void this.pause('媒体连接中断，已停止麦克风。'); },
        this.abort.signal,
      );
      if (generation !== this.generation) { media.close(); return; }
      this.media = media;
      this.patch({ status: 'listening', message: '正在听取回答，可随时暂停或切换人工核对。' });
    } catch (error) {
      if (generation !== this.generation) return;
      const code = (error as { response?: { data?: { error_code?: string } } })?.response?.data?.error_code;
      await this.pause(code === 'voice_session_owned'
        ? '其他页面正在实时对话。确认接管后可以重新开启。'
        : '实时语音未能连接，请检查模型、麦克风权限和本地服务。');
    }
  }
  async receive(raw: unknown) {
    if (!this.grant || !this.state.locked || typeof raw !== 'object' || !raw) return;
    const data = raw as Record<string, unknown>;
    if (data.type === 'expired') { await this.pause('语音授权已失效，请主动重新开启。'); return; }
    if (data.turn_no !== this.turnNo) return;
    if (data.type === 'manual' || (data.type === 'control' && data.intent === 'manual')) {
      const supplemental = this.businessPending || this.sealedTurn === this.turnNo;
      this.patch({ transcript: typeof data.text === 'string' ? data.text : this.state.transcript, supplemental });
      await this.pause(typeof data.reason === 'string' ? data.reason : '请核对文字并手动处理。', true);
      return;
    }
    if (data.type === 'transcript') {
      if (typeof data.revision !== 'number' || data.revision < this.lastRevision) return;
      this.lastRevision = data.revision;
      this.patch({ transcript: typeof data.text === 'string' ? data.text : '',
        supplemental: data.supplemental === true });
    } else if (data.type === 'control') {
      this.patch({ status: 'waiting', transcript: '', message: '继续思考，准备好后直接开口。' });
      if (data.intent === 'repeat') this.media?.send({ type: 'repeat' });
    } else if (data.type === 'candidate') {
      if (this.businessPending || this.sealedTurn === this.turnNo || !['listening', 'waiting'].includes(this.state.status)) return;
      if (typeof data.text !== 'string' || !data.text.trim() || data.text.length > 30000) {
        await this.pause('未取得有效文字，请手动核对。', true); return;
      }
      this.sealedTurn = this.turnNo;
      this.businessPending = true;
      const generation = this.generation;
      this.patch({ status: 'submitting', transcript: data.text, message: '正在保存回答并准备下一题…' });
      try {
        const result = await this.options.onAnswer({ text: data.text, turnNo: this.turnNo,
          turnKey: this.deps.key(), acceptance: automaticAcceptance(this.grant) });
        if (generation !== this.generation) return;
        if (result !== 'saved') await this.pause(result === 'unknown'
          ? '提交结果待确认。请使用原尝试恢复，不能把续说当作新回答。'
          : '回答未保存，请恢复业务状态后手动处理。', true);
      } catch {
        if (generation === this.generation) await this.pause('提交结果待确认，请使用原尝试恢复。', true);
      } finally { this.businessPending = false; }
    }
  }
  questionReady(turnNo: number) {
    if (turnNo === this.turnNo || !this.media || !this.grant || !this.state.locked) return;
    if (this.state.supplemental && this.state.transcript) {
      void this.pause('有待处理的补充文字，请先人工处理。', true); return;
    }
    this.turnNo = turnNo;
    this.lastRevision = -1;
    this.patch({ status: 'listening', transcript: '', supplemental: false, message: '正在听取回答。' });
    this.media.send({ type: 'sync' });
  }
  repeat() { this.media?.send({ type: 'repeat' }); }
  stopForReview() { void this.pause('已停止收音，请核对现有转写；末尾可能尚未识别完整。', true); }
  discardTranscript() {
    if (this.state.locked || this.businessPending) return;
    this.patch({ transcript: '', supplemental: false });
  }
  async pause(message = '已暂停。重新开启需要再次授权本场自动记录。', manual = false) {
    const generation = ++this.generation;
    this.abort?.abort();
    this.abort = undefined;
    this.media?.close();
    this.media = undefined;
    clearInterval(this.heartbeat);
    this.heartbeat = undefined;
    this.patch({ status: manual ? 'manual' : 'paused', message, revocationPending: false });
    const grant = this.grant;
    if (grant) {
      try { await this.deps.action(grant, 'close'); }
      catch {
        if (generation === this.generation) this.patch({ locked: true, revocationPending: true });
        return;
      }
      if (generation !== this.generation) return;
      if (this.grant === grant) this.grant = undefined;
    }
    this.patch({ locked: false });
  }
}

export async function connectMedia(
  grant: VoiceGrant, message: (data: unknown) => void, disconnected: () => void,
  signal: AbortSignal,
): Promise<MediaConnection> {
  const peer = new RTCPeerConnection({ iceServers: [] });
  let stream: MediaStream | undefined;
  let audio: HTMLAudioElement;
  let channel: RTCDataChannel;
  try {
    audio = new Audio();
    channel = peer.createDataChannel('pipecat');
  } catch (error) { peer.close(); throw error; }
  audio.autoplay = true;
  let closed = false;
  let playback = -1;
  let playbackStart = -1;
  let questionId: unknown;
  let ping: ReturnType<typeof setInterval> | undefined;
  const media: MediaConnection = {
    send: (data) => { if (channel.readyState === 'open') channel.send(JSON.stringify(data)); },
    mute: () => {
      audio.muted = true;
      media.send({ type: 'interrupt', playback_id: playback, question_id: questionId,
        position_seconds: Math.max(0, audio.currentTime - playbackStart) });
    },
    close: () => {
      closed = true;
      clearInterval(ping);
      stream?.getTracks().forEach((track) => track.stop());
      audio.pause(); audio.srcObject = null;
      channel.close(); peer.close();
    },
  };
  signal.addEventListener('abort', media.close, { once: true });
  if (signal.aborted) { media.close(); throw new Error('Voice connection cancelled'); }
  try {
    const opened = await navigator.mediaDevices.getUserMedia({ audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
    } });
    stream = opened;
    // getUserMedia cannot be aborted while a native permission dialog is open.
    if (signal.aborted) throw new Error('Voice connection cancelled');
    opened.getTracks().forEach((track) => {
      peer.addTrack(track, opened);
      track.onended = () => { if (!closed) disconnected(); };
    });
    peer.ontrack = (event) => {
      audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      void audio.play().catch(() => { if (!closed) disconnected(); });
    };
    peer.onconnectionstatechange = () => {
      if (!closed && ['failed', 'disconnected', 'closed'].includes(peer.connectionState)) disconnected();
    };
    channel.onclose = () => { if (!closed) disconnected(); };
    channel.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'interrupted') {
          audio.muted = true;
          media.send({ type: 'playback_receipt', playback_id: playback, question_id: questionId,
            position_seconds: Math.max(0, audio.currentTime - playbackStart) });
          playback = data.playback_id;
          playbackStart = -1;
        }
        if (data.type === 'playback' && data.playback_id >= playback) {
          if (playbackStart < 0 || data.playback_id !== playback || questionId !== data.question_id) playbackStart = audio.currentTime;
          questionId = data.question_id;
          playback = data.playback_id; audio.muted = false;
        }
        message(data);
      } catch { /* malformed data is never a business command */ }
    };
    const open = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Media connection timeout')), 15000);
      channel.onopen = () => {
        clearTimeout(timer);
        ping = setInterval(() => { if (channel.readyState === 'open') channel.send('ping'); }, 1000);
        media.send({ type: 'ready' }); resolve();
      };
    });
    // Attach a rejection handler while SDP exchange is still in flight.
    void open.catch(() => undefined);
    await peer.setLocalDescription(await peer.createOffer());
    if (peer.iceGatheringState !== 'complete') await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('ICE gathering timeout')), 6000);
      peer.onicegatheringstatechange = () => {
        if (peer.iceGatheringState === 'complete') { clearTimeout(timer); resolve(); }
      };
    });
    const answer = await voiceOffer(grant, peer.localDescription!.sdp);
    await peer.setRemoteDescription(answer);
    await open;
    return media;
  } catch (error) { signal.removeEventListener('abort', media.close); media.close(); throw error; }
}
