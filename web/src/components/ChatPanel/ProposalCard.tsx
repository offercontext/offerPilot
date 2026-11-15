import { t, useLocale } from '@/i18n';
import { createElement, useEffect, useId, useRef, useState } from 'react';
import { Alert, Button, DatePicker, Input, InputNumber, Select, Switch } from 'antd';
import dayjs from 'dayjs';
import type { PendingAction, PendingActionEditableField } from '@/types/chat';
import { STATUS_LABELS, type ApplicationStatus } from '@/types/application';
import { EVENT_TYPE_LABELS, type ScheduleEventType } from '@/types/event';
import { OFFER_STATUS_LABELS, type OfferStatus } from '@/types/offer';
import { selectEvidence, type EvidenceItem, type EvidenceTarget } from './model';
import { toolMeta } from './capabilities';
import EvidenceList from './EvidenceList';
import {
  actionIdentity,
  changedEditableArgs,
  createProposalReviewState,
  editableFieldsForAction,
  syncProposalReviewState,
  type ProposalReviewState,
} from './proposalDraft';
import styles from './ChatPanel.module.css';

interface Props {
  allowedActions?: readonly string[];
  action: PendingAction;
  loading: boolean;
  evidence: EvidenceItem[];
  onConfirm: (editedArgs?: Record<string, unknown>) => void;
  onCancel: (rejectionFeedback?: string) => void;
  onOpenEvidence?: (target: EvidenceTarget) => void;
}

const FIELD_LABELS: Record<string, string> = {
  get status() { return t("状态"); },
  get company_name() { return t("公司"); },
  get position_name() { return t("岗位"); },
  get base_monthly() { return t("月薪"); },
  get months_per_year() { return t("计薪月数"); },
  get signing_bonus() { return t("签字费"); },
  get equity() { return t("股权／期权"); },
  get perks() { return t("福利"); },
  get assessment() { return t("Offer 评估"); },
  get job_url() { return t("岗位链接"); },
  get source() { return t("来源"); },
  get notes() { return t("备注"); },
  get company() { return t("公司"); },
  get position() { return t("岗位"); },
  get round() { return t("轮次"); },
  get date() { return t("日期"); },
  get questions() { return t("问题记录"); },
  get self_reflection() { return t("自我复盘"); },
  get difficulty_points() { return t("难点短板"); },
  get mood() { return t("感受"); },
  get applied_at() { return t("投递日期"); },
  get title() { return t("标题"); },
  get deadline() { return t("截止时间"); },
  get event_type() { return t("日程类型"); },
  get subtype() { return t("细分类型"); },
  get scheduled_at() { return t("日程时间"); },
  get duration_minutes() { return t("时长"); },
  get location() { return t("地点"); },
  get remind_at() { return t("提醒时间"); },
  get closed_reason() { return t("结束原因"); },
  get allow_placeholder_date() { return t("允许日期占位"); },
  get text() { return t("改写正文"); },
  get submitted_at() { return t("投递时间"); },
  get note() { return t("投递备注"); },
  get stage() { return t("阶段"); },
  get result() { return t("结果"); },
  get feedback_text() { return t("原始反馈"); },
  get reflection_text() { return t("我的复盘"); },
  get next_action_text() { return t("下次行动"); },
  get occurred_at() { return t("发生时间"); },
  get jd_text() { return t("JD 原文"); },
  get source_url() { return t("来源链接"); },
};


const SUBTYPE_LABELS: Record<string, string> = {
  get assessment() { return t("测评"); },
  get technical() { return t("技术面"); },
  get hr() { return t("HR 面"); },
  get final() { return t("终面"); },
  get written() { return t("笔试"); },
};

const OUTCOME_VALUE_LABELS: Record<string, string> = {
  get applied() { return t("已投递"); },
  get screening() { return t("筛选沟通"); },
  get written_test() { return t("笔试"); },
  get interview() { return t("面试"); },
  offer: 'Offer',
  get closed() { return t("流程结束"); },
  get advanced() { return t("进入下一阶段"); },
  get rejected() { return t("未通过"); },
  get withdrawn() { return t("主动退出"); },
  get no_response() { return t("暂无回复"); },
  get offer_received() { return t("收到 Offer"); },
  get other() { return t("其他"); },
  get technical_depth() { return t("技术深度"); },
  get communication() { return t("沟通表达"); },
  get system_design() { return t("系统设计"); },
  get domain_experience() { return t("领域经验"); },
  get leadership() { return t("领导力"); },
  get collaboration() { return t("协作"); },
};

const LONG_REVIEW_FIELDS = new Set(['questions', 'self_reflection', 'difficulty_points', 'mood', 'notes']);

/** Best-effort "从 X 改为 Y" extraction for a before→after chip diff. */
function parseDiff(human: string): { was: string; now: string } | null {
  const m = human.match(
    /从\s*[「『"']?([^」』"'，,]+?)[」』"']?\s*(?:改为|变为|更新为|调整为|设为)\s*[「『"']?([^」』"'。.\s]+)/,
  );
  if (!m) return null;
  return { was: m[1].trim(), now: m[2].trim() };
}

function actionTarget(action: PendingAction): string | null {
  const id = action.args?.id;
  if (typeof id === 'number' || typeof id === 'string') return t("记录 #{id}", { id: id });
  const company = action.args?.company_name;
  const role = action.args?.position_name;
  if (typeof company === 'string' && typeof role === 'string') return `${company} · ${role}`;
  if (typeof company === 'string') return company;
  return null;
}

function proposedValue(action: PendingAction): string | null {
  const status = action.args?.status;
  if (typeof status === 'string' && status.trim()) return t("状态 → {value}", { value: valueLabel(status, 'status', action.tool_name) });
  const title = action.args?.title;
  if (typeof title === 'string' && title.trim()) return t("标题 → {title}", { title: title });
  return null;
}

function fieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

function isApplicationStatus(value: unknown): value is ApplicationStatus {
  return typeof value === 'string' && value in STATUS_LABELS;
}

function isScheduleEventType(value: unknown): value is ScheduleEventType {
  return typeof value === 'string' && value in EVENT_TYPE_LABELS;
}

function valueLabel(value: unknown, field?: string, toolName?: string): string {
  if (value === null || value === undefined || value === '') return t("空");
  if (typeof value === 'boolean') return value ? t("是") : t("否");
  if (field === 'status' && (toolName === 'create_offer' || toolName === 'update_offer')
    && typeof value === 'string' && Object.prototype.hasOwnProperty.call(OFFER_STATUS_LABELS, value)) {
    return OFFER_STATUS_LABELS[value as OfferStatus];
  }
  if (field === 'status' && isApplicationStatus(value)) return STATUS_LABELS[value];
  if (isApplicationStatus(value)) return STATUS_LABELS[value];
  if (field === 'event_type' && isScheduleEventType(value)) return EVENT_TYPE_LABELS[value];
  if (field === 'subtype' && typeof value === 'string') return SUBTYPE_LABELS[value] ?? value;
  if ((field === 'scheduled_at' || field === 'remind_at') && typeof value === 'string') {
    const parsed = dayjs(value);
    return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value;
  }
  if (field === 'duration_minutes') return t("{value} 分钟", { value: String(value) });
  if (typeof value === 'string' && value in OUTCOME_VALUE_LABELS) return OUTCOME_VALUE_LABELS[value];
  return String(value);
}

function dateTimeLabel(value: unknown): string {
  if (typeof value !== 'string') return t("未提供");
  const parsed = dayjs(value);
  return parsed.isValid() ? parsed.format('YYYY-MM-DD HH:mm') : value;
}

function summarizeLongValue(value: unknown, field?: string): string | null {
  if (!field || !LONG_REVIEW_FIELDS.has(field) || typeof value !== 'string') return null;
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (normalized.length <= 80) return null;
  const paragraphs = value
    .split(/\n{2,}|(?:^|\s)(?=#{1,6}\s)|(?:^|\s)(?=\d+[.、]\s)/)
    .map((item) => item.trim())
    .filter(Boolean);
  const paragraphCount = Math.max(1, paragraphs.length);
  return t("新增 {paragraphCount} 段内容 · {count} 字", { paragraphCount: paragraphCount, count: normalized.length });
}

export default function ProposalCard({ action, loading, evidence, onConfirm, onCancel, onOpenEvidence, allowedActions }: Props) {
  useLocale();
  const identity = actionIdentity(action);
  const editorId = `proposal-editor-${useId().replace(/:/g, '')}`;
  const [review, setReview] = useState<ProposalReviewState>(() => createProposalReviewState(action));
  const pendingFocusTargetRef = useRef<'feedback' | 'trigger' | null>(null);

  useEffect(() => {
    setReview((current) => syncProposalReviewState(current, action));
  }, [action, identity]);

  const currentReview = syncProposalReviewState(review, action);
  const editableFields = editableFieldsForAction(action);

  useEffect(() => {
    if (review.identity !== identity || !pendingFocusTargetRef.current) return;
    const targetId =
      pendingFocusTargetRef.current === 'feedback'
        ? `${editorId}-feedback`
        : `${editorId}-reject-trigger`;
    document.getElementById(targetId)?.focus();
    pendingFocusTargetRef.current = null;
  }, [editorId, identity, review.identity, review.rejectOpen]);
  const meta = toolMeta(action.tool_name);
  const diff = parseDiff(action.human);
  const target = action.target?.title ?? actionTarget(action);
  const targetMeta = action.target?.meta;
  const proposed = proposedValue(action);
  const actionEvidence = action.evidence?.map((item) => ({
    id: item.id,
    kind: item.kind as EvidenceItem['kind'],
    title: item.title,
    meta: item.meta,
    snippet: item.snippet,
    source: item.source,
  })) ?? [];
  const visibleEvidence = selectEvidence(evidence.length ? evidence : actionEvidence, 3).visible;
  const changes = action.proposed_changes ?? [];
  const isSubmissionSnapshot = action.tool_name === 'create_application_submission_snapshot';
  const isApplicationOutcome = action.tool_name === 'record_application_outcome';
  const isApplicationRecord = isSubmissionSnapshot || isApplicationOutcome;
  const thinEvidence = visibleEvidence.length === 0 && !isApplicationRecord;
  const longDraftFields = changes.filter((change) => summarizeLongValue(change.after, change.field));
  const isApplicationJd = action.tool_name === 'save_application_jd_version';
  const jdText = typeof action.args?.jd_text === 'string' ? action.args.jd_text : '';
  const applicationJdVersions = action.application_jd;
  const confirmLabel = isApplicationJd
    ? t("确认保存岗位资料")
    : isSubmissionSnapshot
      ? t("确认冻结投递事实")
      : isApplicationOutcome
        ? t("确认记录投递结果")
    : action.tool_name.includes('delete')
    ? t("确认删除")
    : action.tool_name.includes('create') || action.tool_name === 'add_note'
      ? t("确认新建")
      : t("确认更新");
  const isDelete = action.tool_name.includes('delete');

  function updateReview(update: (current: ProposalReviewState) => ProposalReviewState) {
    setReview((current) => update(syncProposalReviewState(current, action)));
  }

  function updateDraft(field: string, value: unknown) {
    updateReview((current) => ({
      ...current,
      draft: { ...current.draft, [field]: value },
    }));
  }

  function setRejectOpen(open: boolean) {
    pendingFocusTargetRef.current = open ? 'feedback' : 'trigger';
    updateReview((current) => ({ ...current, rejectOpen: open }));
  }

  function renderEditorControl(descriptor: PendingActionEditableField) {
    const value = currentReview.draft[descriptor.field];
    const controlId = `${editorId}-${descriptor.field}`;
    const common = { id: controlId, disabled: loading, className: styles.editorControl };

    switch (descriptor.type) {
      case 'enum':
        return (
          <Select
            {...common}
            size="large"
            value={typeof value === 'string' ? value : undefined}
            options={(descriptor.options ?? []).map((option) => ({
              value: option,
              label: valueLabel(option, descriptor.field, action.tool_name),
            }))}
            onChange={(next) => updateDraft(descriptor.field, next)}
          />
        );
      case 'boolean':
        return (
          <label className={styles.switchHitArea} htmlFor={controlId}>
            <Switch
              {...common}
              checked={value === true}
              checkedChildren={t("是")}
              unCheckedChildren={t("否")}
              onChange={(next) => updateDraft(descriptor.field, next)}
            />
          </label>
        );
      case 'number':
        return (
          <InputNumber
            {...common}
            size="large"
            precision={0}
            step={1}
            value={typeof value === 'number' && Number.isFinite(value) ? value : undefined}
            onChange={(next) =>
              typeof next === 'number' && Number.isFinite(next)
                ? updateDraft(descriptor.field, next)
                : updateDraft(
                    descriptor.field,
                    descriptor.clearable === true
                      ? descriptor.clear_value
                      : action.args?.[descriptor.field],
                  )
            }
          />
        );
      case 'datetime': {
        const parsed = typeof value === 'string' && value.trim() ? dayjs(value) : null;
        const dateValue = parsed?.isValid() ? parsed : null;
        return (
          <DatePicker
            {...common}
            size="large"
            showTime
            allowClear={descriptor.clearable === true}
            value={dateValue}
            format="YYYY-MM-DD HH:mm"
            onChange={(next) => {
              const nextValue = next?.isValid()
                ? next.toISOString()
                : descriptor.clearable === true
                  ? descriptor.clear_value
                  : action.args?.[descriptor.field];
              updateDraft(descriptor.field, nextValue);
            }}
          />
        );
      }
      case 'long_text':
        return (
          <Input.TextArea
            {...common}
            size="large"
            value={typeof value === 'string' ? value : ''}
            autoSize={{ minRows: 4, maxRows: 9 }}
            onChange={(event) => updateDraft(descriptor.field, event.target.value)}
          />
        );
      case 'string':
        return (
          <Input
            {...common}
            size="large"
            value={typeof value === 'string' ? value : ''}
            onChange={(event) => updateDraft(descriptor.field, event.target.value)}
          />
        );
    }
  }

  return (
    <div className={styles.proposal} data-pilot-proposal role="group" aria-label={t("AI 修改提议")}>
      <div className={styles.prHead}>
        <span className={styles.prIcon} aria-hidden="true">
          {createElement(meta.icon)}
        </span>
        {t("AI 想执行一个修改操作 · {label}", { label: meta.label })}
      </div>
      <div className={styles.prBody}>
        {isApplicationJd ? (
          <div className={styles.prFacts} aria-label={t("岗位资料确认信息")}>
            <div>
              <span>{t("投递公司")}</span>
              <b>{target ?? t("当前投递")}</b>
            </div>
            <div>
              <span>{t("职位")}</span>
              <b>{targetMeta ?? t("未提供职位信息")}</b>
            </div>
            <div>
              <span>{t("当前版本")}</span>
              <b>{applicationJdVersions?.current_version_number == null
                ? t("暂无版本")
                : `v${applicationJdVersions.current_version_number}`}</b>
            </div>
            <div>
              <span>{t("拟创建版本")}</span>
              <b>{applicationJdVersions ? `v${applicationJdVersions.proposed_version_number}` : t("下一版本")}</b>
            </div>
            <div>
              <span>{t("来源")}</span>
              <b>Pilot</b>
            </div>
            <div>
              <span>{t("字符数")}</span>
              <b>{Array.from(jdText).length}</b>
            </div>
            <div>
              <span>{t("JD 预览")}</span>
              <b className={styles.changeValue} title={jdText}>{jdText}</b>
            </div>
            {typeof action.args?.source_url === 'string' && action.args.source_url.trim() ? (
              <div>
                <span>{t("来源链接（仅文本）")}</span>
                <b className={styles.changeValue}>{action.args.source_url}</b>
              </div>
            ) : null}
            <small>{t("不会访问链接")}</small>
          </div>
        ) : null}
        {isSubmissionSnapshot ? (
          <div className={styles.prFacts} aria-label={t("投递事实确认信息")}>
            <div><span>{t("当前投递")}</span><b>{t("投递 #{value}", { value: String(action.args?.application_id ?? '—') })}</b></div>
            <div><span>{t("实际简历")}</span><b>{t("简历 #{value}", { value: String(action.args?.resume_id ?? '—') })}</b></div>
            <div><span>{t("岗位资料")}</span><b>{t("JD 版本 #{value}", { value: String(action.args?.jd_version_id ?? '—') })}</b></div>
            <div><span>{t("确认材料")}</span><b>{action.args?.material_kit_id ? t("材料包 #{value}", { value: String(action.args.material_kit_id) }) : t("本次未包含")}</b></div>
            <div><span>{t("投递时间")}</span><b>{dateTimeLabel(action.args?.submitted_at)}</b></div>
            <div><span>{t("记录方式")}</span><b>{t("Pilot 人工确认")}</b></div>
            {typeof action.args?.note === 'string' && action.args.note.trim() ? (
              <div><span>{t("投递备注")}</span><b className={styles.changeValue}>{action.args.note}</b></div>
            ) : null}
            <small>{t("确认后生成不可变快照；以后修改简历或 JD 不会覆盖本次档案。")}</small>
          </div>
        ) : null}
        {isApplicationOutcome ? (
          <div className={styles.prFacts} aria-label={t("投递结果确认信息")}>
            <div><span>{t("关联档案")}</span><b>{t("投递事实 #{value}", { value: String(action.args?.submission_snapshot_id ?? '—') })}</b></div>
            <div><span>{t("阶段")}</span><b>{valueLabel(action.args?.stage)}</b></div>
            <div><span>{t("结果")}</span><b>{valueLabel(action.args?.result)}</b></div>
            <div><span>{t("发生时间")}</span><b>{dateTimeLabel(action.args?.occurred_at)}</b></div>
            <div><span>{t("反馈标签")}</span><b>{Array.isArray(action.args?.feedback_tags) && action.args.feedback_tags.length ? action.args.feedback_tags.map((item) => valueLabel(item)).join(' · ') : t("未添加")}</b></div>
            <div><span>{t("记录方式")}</span><b>{t("Pilot 人工确认")}</b></div>
            {typeof action.args?.feedback_text === 'string' && action.args.feedback_text.trim() ? (
              <div><span>{t("原始反馈")}</span><b className={styles.changeValue}>{action.args.feedback_text}</b></div>
            ) : null}
            {typeof action.args?.reflection_text === 'string' && action.args.reflection_text.trim() ? (
              <div><span>{t("我的复盘")}</span><b className={styles.changeValue}>{action.args.reflection_text}</b></div>
            ) : null}
            {typeof action.args?.next_action_text === 'string' && action.args.next_action_text.trim() ? (
              <div><span>{t("下次行动")}</span><b className={styles.changeValue}>{action.args.next_action_text}</b></div>
            ) : null}
            <small>{t("原始反馈与个人复盘会分开保存，不会生成录用概率或能力评分。")}</small>
          </div>
        ) : null}
        {action.workflow ? (
          <div className={styles.workflowHint}>
            <span>
              {t("第 {current_step} / {total_steps} 步 · {current_label}", { current_step: action.workflow.current_step, total_steps: action.workflow.total_steps, current_label: action.workflow.current_label })}
            </span>
            {action.workflow.description ? <b>{action.workflow.description}</b> : null}
          </div>
        ) : null}
        {diff ? (
          <>
            <div className={styles.diff}>
              <span className={styles.chip + ' ' + styles.chipWas}>{diff.was}</span>
              <span className={styles.diffArrow} aria-hidden="true">
                →
              </span>
              <span className={styles.chip + ' ' + styles.chipNow}>{diff.now}</span>
            </div>
            <div className={styles.prSub}>{action.human}</div>
          </>
        ) : (
          <div className={styles.prDesc}>{action.human}</div>
        )}
        {target || proposed ? (
          <div className={styles.prFacts}>
            {target ? (
              <div>
                <span>{t("目标")}</span>
                <b>{targetMeta ? `${target} · ${targetMeta}` : target}</b>
              </div>
            ) : null}
            {proposed ? (
              <div>
                <span>{t("建议变更")}</span>
                <b>{proposed}</b>
              </div>
            ) : null}
          </div>
        ) : null}
        {changes.length ? (
          <div className={styles.changeList}>
            {changes.map((change) => {
              const beforeText = valueLabel(change.before, change.field, action.tool_name);
              const rawAfterText = valueLabel(change.after, change.field, action.tool_name);
              const afterText = summarizeLongValue(change.after, change.field) ?? rawAfterText;
              return (
                <div key={change.field} className={styles.changeRow}>
                  <span>{fieldLabel(change.field)}</span>
                  <b className={styles.changeValue} title={beforeText}>
                    {beforeText}
                  </b>
                  <i aria-hidden="true">→</i>
                  <b className={styles.changeValue} title={rawAfterText}>
                    {afterText}
                  </b>
                </div>
              );
            })}
          </div>
        ) : null}
        {longDraftFields.length ? (
          <div className={styles.draftHint}>{t("长内容已按摘要展示，确认后会完整保存。")}</div>
        ) : null}
        {action.draft_summary?.fields.length ? (
          <div className={styles.draftReview}>
            <div className={styles.panelLabel}>{t("草稿审阅")}</div>
            {action.draft_summary.fields.map((field) => (
              <div key={field.field} className={styles.draftItem}>
                <span>{field.label}</span>
                <b title={field.summary}>{field.summary}</b>
                <i>{t("{characters} 字", { characters: field.characters })}</i>
              </div>
            ))}
          </div>
        ) : null}
        {editableFields.length ? (
          <div className={styles.proposalEditor}>
            <button
              type="button"
              className={styles.editorDisclosure}
              aria-expanded={currentReview.editorOpen}
              aria-controls={editorId}
              disabled={loading}
              onClick={() =>
                updateReview((current) => ({ ...current, editorOpen: !current.editorOpen }))
              }
            >
              <span>{t("编辑建议")}</span>
              <i aria-hidden="true">{currentReview.editorOpen ? t("收起") : t("展开")}</i>
            </button>
            {currentReview.editorOpen ? (
              <div id={editorId} className={styles.editorGrid}>
                <p>{t("只会提交这里实际修改过的字段，其余建议保持不变。")}</p>
                {editableFields.map((descriptor) => {
                  const controlId = `${editorId}-${descriptor.field}`;
                  return (
                    <div key={descriptor.field} className={styles.editorField}>
                      <label htmlFor={controlId}>{fieldLabel(descriptor.field)}</label>
                      {renderEditorControl(descriptor)}
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}
        {action.risk_hint || thinEvidence ? (
          <Alert
            className={styles.prAlert}
            type="warning"
            showIcon
            message={action.risk_hint ?? t("参考依据较少，请确认内容无误后再执行。")}
          />
        ) : null}
        {visibleEvidence.length > 0 ? (
          <div className={styles.prEvidence}>
            <div className={styles.panelLabel}>{t("参考依据")}</div>
            <EvidenceList items={visibleEvidence.slice(0, 3)} compact onOpenEvidence={onOpenEvidence} />
          </div>
        ) : null}
      </div>
      {currentReview.rejectOpen ? (
        <div
          className={styles.rejectPanel}
          role="region"
          aria-live="polite"
          aria-label={isDelete ? t("保留记录确认") : t("拒绝建议确认")}
        >
          <div>
            <b>{isDelete ? t("确认保留这条记录？") : t("最终拒绝这项建议？")}</b>
            <p>
              {isDelete
                ? t("删除不会执行。你可以补充原因，帮助 Pilot 调整后续建议。")
                : t("当前建议不会执行。反馈可选，并会用于后续对话。")}
            </p>
          </div>
          <label htmlFor={`${editorId}-feedback`}>{t("拒绝原因（可选）")}</label>
          <Input.TextArea
            id={`${editorId}-feedback`}
            size="large"
            value={currentReview.feedback}
            maxLength={500}
            showCount
            autoSize={{ minRows: 3, maxRows: 6 }}
            disabled={loading}
            placeholder={t("例如：时间不对，先不要更新")}
            onChange={(event) =>
              updateReview((current) => ({ ...current, feedback: event.target.value }))
            }
          />
          <div className={styles.rejectActions}>
            <button
              type="button"
              className={styles.reviewBack}
              disabled={loading}
              onClick={() => setRejectOpen(false)}
            >{t("返回审核")}</button>
            <Button
              danger
              type="primary"
              loading={loading}
              disabled={allowedActions !== undefined && !allowedActions.includes('reject')}
              onClick={() => onCancel(currentReview.feedback.trim() || undefined)}
            >
              {isDelete ? t("确认不删除") : t("最终拒绝")}
            </Button>
          </div>
        </div>
      ) : (
        <div className={styles.prActions}>
          <Button
            type="primary"
            className="op-ai-btn"
            loading={loading}
            disabled={allowedActions !== undefined && !allowedActions.includes(changedEditableArgs(action, currentReview.draft) ? 'modify' : 'approve')}
            onClick={() => onConfirm(changedEditableArgs(action, currentReview.draft))}
          >
            {confirmLabel}
          </Button>
          <Button
            id={`${editorId}-reject-trigger`}
            disabled={loading}
            onClick={() => setRejectOpen(true)}
          >
            {isDelete ? t("不删除") : t("拒绝建议")}
          </Button>
        </div>
      )}
    </div>
  );
}
