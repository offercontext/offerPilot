import type { ResumeAuditFinding } from './resumeEvidenceAudit';
import type { ResumeContent } from '@/types/resume';

const MAX_DEPTH = 96;
const MAX_NODES = 10_000;
const MAX_TEXT_CODE_POINTS = 400;
const FORBIDDEN_SEGMENTS = new Set(['__proto__', 'prototype', 'constructor']);

export function isSupplementableResumeFinding(finding: ResumeAuditFinding): boolean {
  if (finding.status !== 'review' || typeof finding.source?.excerpt !== 'string') return false;
  try {
    return parsePointer(finding.source.path).length > 0;
  } catch {
    return false;
  }
}

export function validateSupplementText(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error('最终表述不能为空');
  const length = Array.from(trimmed).length;
  if (length < 2) throw new Error('最终表述至少需要 2 个字符');
  if (length > MAX_TEXT_CODE_POINTS) throw new Error(`最终表述不能超过 ${MAX_TEXT_CODE_POINTS} 个字符`);
  return trimmed;
}

export function applyResumeFactSupplement(
  content: unknown,
  pointer: string,
  expectedExcerpt: string,
  replacement: string,
): ResumeContent {
  const segments = parsePointer(pointer);
  if (segments.length === 0) throw new Error('不能替换简历根节点');
  const nextValue = validateSupplementText(replacement);
  const budget = { nodes: 0 };
  const cloned = cloneJsonValue(content, 0, budget);
  if (!isPlainRecord(cloned)) throw new Error('简历结构不是可安全编辑的对象');

  let current: unknown = cloned;
  for (let index = 0; index < segments.length - 1; index += 1) {
    current = readChild(current, segments[index]);
  }

  const finalSegment = segments[segments.length - 1];
  const currentValue = readChild(current, finalSegment);
  if (typeof currentValue !== 'string') throw new Error('目标字段不是字符串，不能在事实补充工作台中修改');
  if (currentValue !== expectedExcerpt) throw new Error('原文已变化，请重新运行简历事实体检');
  writeChild(current, finalSegment, nextValue);
  return cloned as ResumeContent;
}

function parsePointer(pointer: string): string[] {
  if (pointer === '') return [];
  if (!pointer.startsWith('/')) throw new Error('字段路径不是合法 JSON Pointer');
  return pointer.slice(1).split('/').map((raw) => {
    if (/~(?![01])/u.test(raw)) throw new Error('字段路径包含非法转义');
    const decoded = raw.replace(/~1/gu, '/').replace(/~0/gu, '~');
    if (FORBIDDEN_SEGMENTS.has(decoded)) throw new Error('字段路径包含不安全属性');
    return decoded;
  });
}

function cloneJsonValue(value: unknown, depth: number, budget: { nodes: number }): unknown {
  budget.nodes += 1;
  if (budget.nodes > MAX_NODES) throw new Error('简历结构过大，无法安全补充');
  if (depth > MAX_DEPTH) throw new Error('简历结构过深，无法安全补充');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value !== 'object') throw new Error('简历包含不支持的字段值');

  let prototype: object | null;
  let descriptors: PropertyDescriptorMap;
  let symbols: symbol[];
  try {
    prototype = Object.getPrototypeOf(value);
    descriptors = Object.getOwnPropertyDescriptors(value);
    symbols = Object.getOwnPropertySymbols(value);
  } catch {
    throw new Error('简历包含无法安全读取的结构');
  }
  if (symbols.length > 0) throw new Error('简历包含不支持的 Symbol 字段');

  if (Array.isArray(value)) {
    if (prototype !== Array.prototype) throw new Error('简历包含异常数组');
    const lengthDescriptor = descriptors.length;
    if (!lengthDescriptor || typeof lengthDescriptor.value !== 'number') throw new Error('简历包含异常数组');
    const length = lengthDescriptor.value;
    const output: unknown[] = [];
    for (let index = 0; index < length; index += 1) {
      const key = String(index);
      const descriptor = descriptors[key];
      if (!descriptor || !('value' in descriptor) || !descriptor.enumerable) {
        throw new Error('简历包含稀疏或异常数组');
      }
      output.push(cloneJsonValue(descriptor.value, depth + 1, budget));
    }
    const allowedKeys = new Set(['length', ...Array.from({ length }, (_, index) => String(index))]);
    if (Object.keys(descriptors).some((key) => !allowedKeys.has(key))) throw new Error('简历包含异常数组属性');
    return output;
  }

  if (prototype !== Object.prototype && prototype !== null) throw new Error('简历包含非普通对象');
  const output: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of Object.keys(descriptors).sort()) {
    if (FORBIDDEN_SEGMENTS.has(key)) throw new Error('简历包含不安全属性');
    const descriptor = descriptors[key];
    if (!descriptor.enumerable || !('value' in descriptor)) throw new Error('简历包含访问器或隐藏属性');
    output[key] = cloneJsonValue(descriptor.value, depth + 1, budget);
  }
  return output;
}

function readChild(container: unknown, segment: string): unknown {
  if (Array.isArray(container)) {
    const index = parseArrayIndex(segment, container.length);
    return container[index];
  }
  if (!isPlainRecord(container)) throw new Error('字段路径穿过了非容器值');
  if (!Object.prototype.hasOwnProperty.call(container, segment)) throw new Error('字段路径不存在');
  return container[segment];
}

function writeChild(container: unknown, segment: string, value: string): void {
  if (Array.isArray(container)) {
    container[parseArrayIndex(segment, container.length)] = value;
    return;
  }
  if (!isPlainRecord(container) || !Object.prototype.hasOwnProperty.call(container, segment)) {
    throw new Error('字段路径不存在');
  }
  container[segment] = value;
}

function parseArrayIndex(segment: string, length: number): number {
  if (!/^(0|[1-9][0-9]*)$/u.test(segment)) throw new Error('数组索引不是规范格式');
  const index = Number(segment);
  if (!Number.isSafeInteger(index) || index >= length) throw new Error('数组索引超出范围');
  return index;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  try {
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  } catch {
    return false;
  }
}
