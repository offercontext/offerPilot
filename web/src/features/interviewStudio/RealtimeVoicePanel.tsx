import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Space } from 'antd';
import { voiceCapabilities, type VoiceCapabilities } from '@/services/interviewVoice';
import type { RealtimeState } from '@/features/mockInterviewVoice/realtimeVoiceController';
import styles from './ContinuousVoiceModePanel.module.css';

interface Props {
  state: RealtimeState;
  disabled: boolean;
  onStart: (revision: string, takeover: boolean) => void;
  onPause: () => void;
  onStop: () => void;
  onRepeat: () => void;
  onManual: (text: string) => void;
  onDiscard: () => void;
}

export default function RealtimeVoicePanel(props: Props) {
  const [capabilities, setCapabilities] = useState<VoiceCapabilities>();
  const [consent, setConsent] = useState(false);
  const [takeover, setTakeover] = useState(false);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState(false);
  const refresh = () => {
    void voiceCapabilities().then(setCapabilities).catch(() => setError('无法检查实时语音服务。'));
  };
  useEffect(refresh, []);
  useEffect(() => { if (!props.state.locked) setConsent(false); }, [props.state.locked]);
  const running = ['connecting', 'listening', 'waiting', 'submitting'].includes(props.state.status);
  return (
    <section aria-label="实时对话" className={styles.panel}>
      <details open={expanded || running || props.state.locked} onToggle={(event) => {
        if (!event.currentTarget.open && (running || props.state.locked)) props.onPause();
        if (!running && !props.state.locked) setExpanded(event.currentTarget.open);
      }}>
      <summary style={{ cursor: 'pointer' }}><strong>实时对话 · 实验功能</strong>{!running ? ' · 自动接话与打断' : ''}</summary>
      <p className={styles.description}>自动识别回答结束并记录，可打断播报。音频会发送到 OfferPilot 所在设备或服务器，由本地模型完成识别、轮次检测与播报；不保存原始音频，不切换到云端语音。面试出题仍使用当前配置的 AI 服务。</p>
      {!running && !props.state.locked ? (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Checkbox checked={consent} disabled={props.disabled} onChange={(event) => setConsent(event.target.checked)}>授权本次连接自动记录回答（未经逐轮人工校对）</Checkbox>
          {props.state.message.includes('接管') ? <Checkbox checked={takeover} onChange={(event) => setTakeover(event.target.checked)}>接管此练习，停止其他页面的实时语音授权</Checkbox> : null}
          <Button disabled={props.disabled || Boolean(props.state.transcript.trim()) || !consent || !capabilities?.available} onClick={() => {
            props.onStart(capabilities!.consent_revision, takeover); setTakeover(false);
          }}>开启实时对话</Button>
        </Space>
      ) : (
        <Space wrap>
          <Button onClick={props.onPause}>暂停并撤销自动记录</Button>
          <Button disabled={!running || props.state.status === 'submitting'} onClick={props.onRepeat}>重播当前题</Button>
          <Button disabled={!running} onClick={props.onStop}>结束收音并核对文字</Button>
        </Space>
      )}
      {!capabilities?.available ? <p>{error || capabilities?.reason || '正在检查语音服务…'} <Button type="link" size="small" onClick={refresh}>重新检查</Button></p> : null}
      {props.state.message ? <p role="status" aria-live="polite" aria-atomic="true">{props.state.message}</p> : null}
      {props.state.transcript ? (
        <div>
          <strong>{props.state.supplemental ? '待处理补充（未保存）' : '实时转写（可能有误）'}</strong>
          <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{props.state.transcript}</p>
          {!running && !props.state.locked && !props.state.supplemental ? <Button disabled={props.disabled} onClick={() => props.onManual(props.state.transcript)}>带入文字框手动核对</Button> : null}
          {!running && !props.state.locked ? <Button type="link" disabled={props.disabled} onClick={props.onDiscard}>{props.state.supplemental ? '已阅读补充，继续当前题' : '放弃此转写，重新收音'}</Button> : null}
          {props.state.supplemental ? <Alert type="warning" message="原回答若已保存，不能覆盖；这些补充不会自动进入下一题。需要修正原回答时请重新练习。" /> : null}
        </div>
      ) : null}
      </details>
    </section>
  );
}
