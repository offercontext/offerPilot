import { t } from '@/i18n';
import { storySourceLabel } from './storySourcePresentation';

export function resumeEvidenceLocation(path: string): string {
  const relative = path.replace(/^\/resume(?=\/)/, '');
  return storySourceLabel('resume_version', relative.startsWith('/content_json/') ? relative : `/content_json${relative}`);
}

export function evidenceSourceLabel(source: string): string {
  const labels: Record<string, string> = {
    jd: t("岗位描述"),
    resume: t("选定简历"),
    knowledge_evidence: t("已确认知识依据"),
    confirmed_readiness_feedback: t("已确认复盘重点"),
    turn: t("先前回答"),
    user_assertion: t("我的补充说明"),
    evidence_bundle: t("投递参考内容"),
  };
  return Object.prototype.hasOwnProperty.call(labels, source) ? labels[source] : t("参考依据");
}

export function evidenceLocationLabel(source: string, path: string): string {
  if (source === 'resume' || (source === 'evidence_bundle' && path.startsWith('/resume/'))) return resumeEvidenceLocation(path);
  if (source === 'jd' || (source === 'evidence_bundle' && path === '/jd/text')) return t("岗位要求");
  const turn = /^\/turns\/(\d+)\/answer$/.exec(path);
  if (source === 'turn') return turn ? t("第 {value} 轮回答", { value: Number(turn[1]) }) : t("回答片段");
  const assertion = /^\/user_assertions\/(\d+)\/text$/.exec(path);
  if (source === 'user_assertion') return assertion ? t("我的补充说明 {value}", { value: Number(assertion[1]) + 1 }) : t("我的补充说明");
  if (source === 'confirmed_readiness_feedback') {
    const feedback = /^\/readiness_feedback\/(\d+)\/(?:statement|evidence\/(\d+)\/excerpt)$/.exec(path);
    return feedback ? t("复盘准备重点 {value}{value2}", { value: Number(feedback[1]) + 1, value2: feedback[2] === undefined ? '' : t(" · 证据 {value}", { value: Number(feedback[2]) + 1 }) }) : t("准备重点片段");
  }
  return source === 'knowledge_evidence' ? t("知识证据片段") : t("来源片段");
}
