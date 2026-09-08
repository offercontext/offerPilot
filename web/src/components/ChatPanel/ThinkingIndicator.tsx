import { useEffect, useState } from 'react';
import styles from './ChatPanel.module.css';

const WAITING_STEPS = [
  '正在理解你的问题',
  '正在调用工具读取上下文',
  '正在等待模型返回结果',
  '正在整理结论和下一步建议',
];

interface Props {
  label?: string;
}

export default function ThinkingIndicator({ label }: Props) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setStep((value) => (value + 1) % WAITING_STEPS.length), 1800);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className={styles.thinking} role="status" aria-live="polite">
      <span className={styles.dots} aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>{label ?? WAITING_STEPS[step]}...</span>
    </div>
  );
}
