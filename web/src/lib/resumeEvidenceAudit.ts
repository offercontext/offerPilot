import type { Resume } from '@/types/resume';

export type ResumeAuditStatus = 'present' | 'review' | 'unknown';

export type ResumeAuditCategory = 'structure' | 'experience' | 'facts' | 'format';

export interface ResumeAuditSource {
  path: string;
  excerpt?: string;
  /** Complete saved leaf value for exact CAS checks; never rendered as the compact preview. */
  fullText?: string;
}

export interface ResumeAuditFinding {
  id: string;
  category: ResumeAuditCategory;
  status: ResumeAuditStatus;
  title: string;
  explanation: string;
  source?: ResumeAuditSource;
}

export interface ResumeAuditResult {
  findings: ResumeAuditFinding[];
  counts: Record<ResumeAuditStatus, number>;
}

type CoreField = 'contact' | 'education' | 'experience' | 'projects' | 'skills' | 'career_intent';
type Inspection = 'empty' | 'present' | 'unknown';
type InspectionPosition = 'field' | 'property' | 'array-item';

interface Bullet {
  path: string;
  text: string;
}

interface ExperienceScan {
  bullets: Bullet[];
  hasItems: boolean;
  missingBulletPath?: string;
  unknownPath?: string;
}

const CORE_FIELDS: readonly CoreField[] = [
  'contact',
  'education',
  'experience',
  'projects',
  'skills',
  'career_intent',
];

const BULLET_KEYS = ['highlights', 'bullets', 'achievements'] as const;
const MAX_BULLET_CODE_POINTS = 240;
const MAX_EXCERPT_CODE_POINTS = 160;

const CORE_FIELD_LABELS: Record<CoreField, string> = {
  contact: '联系方式',
  education: '教育经历',
  experience: '工作经历',
  projects: '项目经历',
  skills: '技能清单',
  career_intent: '求职意向',
};

const CORE_FIELD_IDS: Record<CoreField, string> = {
  contact: 'contact',
  education: 'education',
  experience: 'experience',
  projects: 'projects',
  skills: 'skills',
  career_intent: 'career-intent',
};

const CORE_FIELD_SHAPES: Record<CoreField, 'record' | 'array' | 'flexible'> = {
  contact: 'record',
  education: 'array',
  experience: 'array',
  projects: 'array',
  skills: 'flexible',
  career_intent: 'record',
};

export function auditResume(resume: Resume): ResumeAuditResult {
  const rawContent = resume && (typeof resume === 'object' || typeof resume === 'function')
    ? readProperty(resume as unknown as Record<string, unknown>, 'content_json')
    : undefined;
  if (!isPlainRecord(rawContent)) {
    return buildResult([
      {
        id: 'structure-content-json',
        category: 'structure',
        status: 'unknown',
        title: '简历内容结构',
        explanation: '当前简历内容不足，暂时无法完成结构化体检。',
      },
      formatBoundaryFinding(),
    ]);
  }

  const structureFindings = CORE_FIELDS.map((field) => auditCoreField(rawContent, field));
  const experienceValue = readProperty(rawContent, 'experience');
  const experienceScan = scanExperience(experienceValue);
  const experienceFindings = auditExperienceFindings(experienceValue, experienceScan);
  const findings = [
    ...structureFindings,
    ...experienceFindings,
    ...auditQuantificationFinding(experienceScan),
    formatBoundaryFinding(),
  ];

  return buildResult(findings);
}

function auditCoreField(content: Record<string, unknown>, field: CoreField): ResumeAuditFinding {
  const value = readProperty(content, field);
  const status = classifyCoreField(value, CORE_FIELD_SHAPES[field]);
  const label = CORE_FIELD_LABELS[field];
  const explanation = status === 'present'
    ? `已检测到可识别的${label}信息。`
    : status === 'review'
      ? `当前${label}字段为空，建议检查是否需要补充。`
      : `当前${label}字段结构无法安全识别。`;

  return {
    id: `structure-${CORE_FIELD_IDS[field]}`,
    category: 'structure',
    status,
    title: label,
    explanation,
    source: { path: `/${field}` },
  };
}

function classifyCoreField(value: unknown, shape: 'record' | 'array' | 'flexible'): ResumeAuditStatus {
  try {
    if (typeof value === 'undefined') return 'review';

    if (shape === 'record' && !isPlainRecord(value)) return 'unknown';
    if (shape === 'array' && !Array.isArray(value)) return 'unknown';
    if (shape === 'flexible' && !isPlainRecord(value) && !Array.isArray(value) && typeof value !== 'string') {
      return 'unknown';
    }

    const inspection = inspectValue(value, new WeakSet<object>());
    return inspection === 'empty' ? 'review' : inspection;
  } catch {
    return 'unknown';
  }
}

function scanExperience(value: unknown): ExperienceScan {
  if (typeof value === 'undefined') return { bullets: [], hasItems: false };

  const bullets: Bullet[] = [];
  let missingBulletPath: string | undefined;
  let unknownPath: string | undefined;

  try {
    if (!Array.isArray(value)) return { bullets: [], hasItems: true, unknownPath: '/experience' };

    const experienceLength = value.length;
    for (let experienceIndex = 0; experienceIndex < experienceLength; experienceIndex += 1) {
      const item = value[experienceIndex];
      const itemPath = `/experience/${experienceIndex}`;

      if (typeof item === 'string') {
        bullets.push({ path: itemPath, text: item });
        continue;
      }

      if (!isPlainRecord(item)) {
        unknownPath ??= itemPath;
        continue;
      }

      let itemHasBullet = false;
      let itemHasUnknown = false;
      for (const key of BULLET_KEYS) {
        const read = readOwnProperty(item, key);
        if (!read.exists) continue;
        if (!Array.isArray(read.value)) {
          unknownPath ??= `${itemPath}/${key}`;
          itemHasUnknown = true;
          continue;
        }
        const bulletValues = read.value;
        for (let bulletIndex = 0; bulletIndex < bulletValues.length; bulletIndex += 1) {
          const bullet = bulletValues[bulletIndex];
          const bulletPath = `${itemPath}/${key}/${bulletIndex}`;
          if (typeof bullet === 'string') {
            bullets.push({ path: bulletPath, text: bullet });
            itemHasBullet = true;
          } else {
            unknownPath ??= bulletPath;
            itemHasUnknown = true;
          }
        }
      }

      if (!itemHasBullet && !itemHasUnknown) missingBulletPath ??= itemPath;
    }
    return { bullets, hasItems: experienceLength > 0, missingBulletPath, unknownPath };
  } catch {
    return {
      bullets,
      hasItems: true,
      missingBulletPath,
      unknownPath: unknownPath ?? '/experience',
    };
  }
}

function auditExperienceFindings(value: unknown, scan: ExperienceScan): ResumeAuditFinding[] {
  if (typeof value === 'undefined' || !scan.hasItems) return [];

  const findings: Array<ResumeAuditFinding | null> = [];
  const blankBullet = scan.bullets.find((bullet) => bullet.text.trim().length === 0);
  findings.push(blankBullet
    ? {
        id: 'experience-empty-bullet',
        category: 'experience',
        status: 'review',
        title: '空白经历要点',
        explanation: '存在纯空白的经历要点，建议检查这条内容是否需要补充或删除。',
        source: sourceFor(blankBullet),
      }
    : null);

  const seen = new Map<string, Bullet>();
  let duplicateBullet: Bullet | undefined;
  for (const bullet of scan.bullets) {
    const normalized = bullet.text.trim();
    if (!normalized) continue;
    const previous = seen.get(normalized);
    if (previous) {
      duplicateBullet = bullet;
      break;
    }
    seen.set(normalized, bullet);
  }
  findings.push(duplicateBullet
    ? {
        id: 'experience-duplicate-bullet',
        category: 'experience',
        status: 'review',
        title: '重复经历要点',
        explanation: '检测到内容完全相同的经历要点，建议检查是否需要合并或区分。',
        source: sourceFor(duplicateBullet),
      }
    : null);

  const longBullet = scan.bullets.find((bullet) => codePointLength(bullet.text) > MAX_BULLET_CODE_POINTS);
  findings.push(longBullet
    ? {
        id: 'experience-long-bullet',
        category: 'experience',
        status: 'review',
        title: '异常长经历要点',
        explanation: `这条经历要点超过 ${MAX_BULLET_CODE_POINTS} 个 Unicode code point，建议检查是否包含过长段落。`,
        source: sourceFor(longBullet),
      }
    : null);

  if (scan.missingBulletPath) {
    findings.push({
      id: 'experience-bullets-missing',
      category: 'experience',
      status: 'review',
      title: '缺少可识别经历要点',
      explanation: '经历项存在，但当前没有可识别的 bullet、achievement 或 highlight 字符串集合。',
      source: { path: scan.missingBulletPath },
    });
  } else {
    findings.push(null);
  }

  findings.push(scan.unknownPath
    ? {
        id: 'experience-bullets-unknown',
        category: 'experience',
        status: 'unknown',
        title: '经历要点结构无法判断',
        explanation: '当前经历结构中混入无法安全识别的元素，暂时无法完整判断经历要点。',
        source: { path: scan.unknownPath },
      }
    : null);

  return findings.filter((finding): finding is ResumeAuditFinding => finding !== null);
}

function auditQuantificationFinding(scan: ExperienceScan): ResumeAuditFinding[] {
  if (scan.bullets.length === 0) return [];

  const evidenceBullet = scan.bullets.find((bullet) => bullet.text.trim().length > 0);
  if (!evidenceBullet) return [];

  const quantifiedBullet = scan.bullets.find((bullet) => /[0-9]/.test(bullet.text));
  if (quantifiedBullet) {
    return [{
      id: 'facts-quantification',
      category: 'facts',
      status: 'present',
      title: '存在量化表达',
      explanation: '当前可识别经历要点中出现了阿拉伯数字，仅表示存在量化表达，不代表真实或充分。',
      source: sourceFor(quantifiedBullet),
    }];
  }

  return [{
    id: 'facts-quantification',
    category: 'facts',
    status: 'review',
    title: '可补充真实事实',
    explanation: '当前可识别经历要点中没有出现阿拉伯数字；如有真实数据，可以补充数量、规模、频率、时间或结果。',
    ...(evidenceBullet ? { source: sourceFor(evidenceBullet) } : {}),
  }];
}

function formatBoundaryFinding(): ResumeAuditFinding {
  return {
    id: 'format-visual-unknown',
    category: 'format',
    status: 'unknown',
    title: '版式能力边界',
    explanation: '当前结构化内容无法判断原始文件的字体、表格、图片、页眉页脚、分页和 ATS 解析效果。',
  };
}

function buildResult(findings: ResumeAuditFinding[]): ResumeAuditResult {
  const counts: Record<ResumeAuditStatus, number> = { present: 0, review: 0, unknown: 0 };
  for (const finding of findings) counts[finding.status] += 1;
  return { findings, counts };
}

function inspectValue(
  value: unknown,
  seen: WeakSet<object>,
  position: InspectionPosition = 'field',
): Inspection {
  if (typeof value === 'string') return value.trim().length > 0 ? 'present' : 'empty';
  if (typeof value === 'boolean') return 'present';
  if (typeof value === 'number') return Number.isFinite(value) ? 'present' : 'unknown';
  if (value === null || typeof value === 'undefined') return 'unknown';
  if (typeof value !== 'object') return 'unknown';
  if (position === 'array-item' && Array.isArray(value)) return 'unknown';

  const objectValue = value as object;
  if (seen.has(objectValue)) return 'unknown';
  seen.add(objectValue);

  let result: Inspection;
  try {
    if (Array.isArray(value)) {
      result = value.length === 0
        ? emptyContainerInspection(position)
        : inspectChildren(value, seen, 'array-item');
    } else if (!isPlainRecord(value)) {
      result = 'unknown';
    } else {
      const keys = Object.keys(value);
      result = keys.length === 0
        ? emptyContainerInspection(position)
        : inspectChildren(keys.map((key) => readProperty(value, key)), seen, 'property');
    }
  } catch {
    result = 'unknown';
  }

  seen.delete(objectValue);
  return result;
}

function inspectChildren(
  values: unknown[],
  seen: WeakSet<object>,
  childPosition: InspectionPosition,
): Inspection {
  if (values.length === 0) return 'empty';
  let hasPresent = false;
  for (const value of values) {
    const child = inspectValue(value, seen, childPosition);
    if (child === 'unknown') return 'unknown';
    if (child === 'present') hasPresent = true;
  }
  return hasPresent ? 'present' : 'empty';
}

function emptyContainerInspection(position: InspectionPosition): Inspection {
  return position === 'array-item' ? 'unknown' : 'empty';
}

function readProperty(value: Record<string, unknown>, key: string): unknown {
  try {
    return value[key];
  } catch {
    return Symbol('unreadable');
  }
}

function readOwnProperty(value: Record<string, unknown>, key: string): { exists: boolean; value?: unknown } {
  try {
    if (!Object.prototype.hasOwnProperty.call(value, key)) return { exists: false };
    return { exists: true, value: value[key] };
  } catch {
    return { exists: true, value: Symbol('unreadable') };
  }
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false;
  try {
    if (Array.isArray(value)) return false;
    const prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  } catch {
    return false;
  }
}

function sourceFor(bullet: Bullet): ResumeAuditSource {
  return {
    path: bullet.path,
    excerpt: truncateExcerpt(bullet.text),
    fullText: bullet.text,
  };
}

function codePointLength(value: string): number {
  return Array.from(value).length;
}

function truncateExcerpt(value: string): string {
  const codePoints = Array.from(value);
  if (codePoints.length <= MAX_EXCERPT_CODE_POINTS) return value;
  return `${codePoints.slice(0, MAX_EXCERPT_CODE_POINTS).join('')}…`;
}
