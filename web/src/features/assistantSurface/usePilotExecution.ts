import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getPilotExecution, interruptPilotExecution } from '@/services/chat';
import type { PilotExecution, PilotInterruptResult } from '@/types/chat';

type StopCommand = { target: PilotExecution; commandId: string };
const memory = new Map<string, StopCommand>();
const prefix = 'offerpilot.pending_interrupt.v2.';
const key = (id: string) => `${prefix}${id}`;
function readCommand(id: number): StopCommand | undefined {
  const commands = new Map(memory);
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const storedKey = localStorage.key(index);
      if (!storedKey?.startsWith(prefix)) continue;
      try {
        const value = JSON.parse(localStorage.getItem(storedKey) || 'null') as StopCommand | null;
        if (value && value.target.conversation_id === id && typeof value.target.turn_id === 'string'
          && Number.isSafeInteger(value.target.execution_generation) && value.target.execution_generation > 0
          && typeof value.commandId === 'string' && storedKey === key(value.commandId)) commands.set(value.commandId, value);
      } catch { /* Ignore an unrelated malformed storage record. */ }
    }
  } catch { /* Keep the current page's identity if storage is unavailable. */ }
  return [...commands.values()].filter((command) => command.target.conversation_id === id)
    .sort((a, b) => a.commandId.localeCompare(b.commandId))[0];
}
function saveCommand(id: number, command: StopCommand) {
  if (id !== command.target.conversation_id) throw new Error('interrupt_scope_mismatch');
  try { localStorage.setItem(key(command.commandId), JSON.stringify(command)); memory.delete(command.commandId); }
  catch { memory.set(command.commandId, command); }
}
function clearCommand(id: number, commandId: string) {
  if (memory.get(commandId)?.target.conversation_id === id) memory.delete(commandId);
  try { localStorage.removeItem(key(commandId)); } catch { /* Result is known in this page. */ }
}
export function sameExecution(a: PilotExecution | null, b: PilotExecution): boolean {
  return a?.conversation_id === b.conversation_id && a.turn_id === b.turn_id
    && a.execution_generation === b.execution_generation;
}
const messages: Record<PilotInterruptResult['status'], string> = {
  stopped: '任务已停止。已提交的更改仍保留，可在对应记录中撤销。',
  already_ended: '任务已经结束，已保存的记录仍保留。',
  generation_changed: '原执行已经结束，当前执行未受影响。',
  result_unknown: '执行结果尚未确认，请读取已保存的任务记录。',
  no_active_execution: '当前没有正在执行的任务，待确认操作仍保留。',
};

/** Read and interrupt only: this hook never resumes or launches execution. */
export function usePilotExecution(conversationId: number | undefined, onStopped: (target: PilotExecution) => void) {
  const [execution, setExecution] = useState<PilotExecution | null>(null);
  const [retry, setRetry] = useState<StopCommand>();
  const [stopping, setStopping] = useState(false);
  const [stopMessage, setStopMessage] = useState('');
  const current = useRef(conversationId);
  const executionRef = useRef(execution);
  const callback = useRef(onStopped);
  const inFlight = useRef(new Set<number>());
  const revision = useRef(0);
  current.current = conversationId;
  executionRef.current = execution;
  callback.current = onStopped;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    setExecution((value) => value?.conversation_id === conversationId ? value : null);
    setRetry(conversationId === undefined ? undefined : readCommand(conversationId));
    setStopMessage('');
    setStopping(conversationId !== undefined && inFlight.current.has(conversationId));
    const read = async () => {
      if (conversationId === undefined) return;
      const startedAt = revision.current;
      try {
        const value = await getPilotExecution(conversationId);
        if (!cancelled && startedAt === revision.current) {
          if (value && sameExecution(executionRef.current, value) && executionRef.current?.state === 'running'
            && ['stopped', 'interrupted', 'result_unknown'].includes(value.state)) {
            callback.current(value);
            setStopMessage(value.state === 'stopped' ? messages.stopped : value.state === 'result_unknown'
              ? messages.result_unknown : '执行已中断，已保存的记录仍保留。');
          }
          setExecution(value);
          setRetry(readCommand(conversationId));
        }
      } catch { /* Preserve the last exact target; POST still checks its generation. */ }
      if (!cancelled) timer = setTimeout(() => void read(), 2000);
    };
    void read();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [conversationId]);

  const acceptExecution = useCallback((value: PilotExecution) => {
    revision.current += 1;
    executionRef.current = value;
    setExecution(value);
    setStopMessage('');
  }, []);

  const stop = useCallback(async () => {
    const id = current.current;
    if (id === undefined || inFlight.current.has(id)) return;
    let command = readCommand(id);
    const target = executionRef.current;
    if (!command) {
      if (target?.conversation_id !== id || target.state !== 'running') return;
      command = { target, commandId: crypto.randomUUID() };
      saveCommand(id, command);
    }
    inFlight.current.add(id);
    setRetry(command);
    setStopping(true);
    setStopMessage('正在停止任务…');
    try {
      const result = await interruptPilotExecution(command.target, command.commandId);
      if (result.command_id !== command.commandId || result.turn_id !== command.target.turn_id
        || result.execution_generation !== command.target.execution_generation || !(result.status in messages)) {
        throw new Error('interrupt_response_mismatch');
      }
      clearCommand(id, command.commandId);
      if (current.current !== id) return;
      setRetry(readCommand(id));
      if (!sameExecution(executionRef.current, command.target)) return;
      setStopMessage(messages[result.status]);
      revision.current += 1;
      if (sameExecution(executionRef.current, command.target)) {
        if (result.status === 'stopped' || result.status === 'already_ended') {
          setExecution({ ...command.target, state: result.status === 'stopped' ? 'stopped' : 'completed' });
          callback.current(command.target);
        }
      }
    } catch {
      if (current.current === id) setStopMessage('停止结果未确认，请重试原停止命令。');
    } finally {
      inFlight.current.delete(id);
      if (current.current === id) setStopping(false);
    }
  }, []);

  return useMemo(() => ({ execution, stopping, stopMessage, stop, acceptExecution, retryingStop: Boolean(retry),
    canStop: Boolean(retry) || execution?.state === 'running' }), [execution, stopping, stopMessage, stop, acceptExecution, retry]);
}
