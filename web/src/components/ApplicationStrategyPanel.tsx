import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Input, Select, Space, Spin } from 'antd';
import { isAxiosError } from 'axios';
import { t, useLocale } from '@/i18n';
import { applicationEvidenceTime } from '@/lib/applicationCommunicationDraft';
import { listApplications } from '@/services/applications';
import {
  getStrategy, listStrategyArchives, writeStrategy,
  type StrategyAction, type StrategyCommand, type StrategyDecision, type StrategyOutcome,
} from '@/services/applicationStrategy';
import styles from './ApplicationOutcomeDrawer.module.css';

interface Props {
  applicationId: number;
  outcomeId: number;
  initialProposal: string;
  sourceLabel: string;
  timeZone: string;
}

const decisionLabels = () => ({
  keep: t("保留改法"), revise: t("修改改法"), drop: t("放弃改法"),
});

function resultLabel(outcome: StrategyOutcome, timeZone: string) {
  const time = applicationEvidenceTime(outcome.occurred_at, timeZone, 'archive_utc') ?? t("时间不可用");
  return `${time} · ${outcome.result === 'advanced' ? t("初筛通过") : t("初筛未通过")} · ${t("结果 #")}${outcome.id}`;
}

// Only a button click writes. The query key is tied to the exact original result;
// a different drawer/application mounted during a request never receives its state.
export default function ApplicationStrategyPanel({ applicationId, outcomeId, initialProposal, sourceLabel, timeZone }: Props) {
  useLocale();
  const queryClient = useQueryClient();
  const statusKey = ['application-strategy', applicationId, outcomeId] as const;
  const status = useQuery({
    queryKey: statusKey, queryFn: ({ signal }) => getStrategy(applicationId, outcomeId, signal),
    retry: false, staleTime: 0, refetchOnMount: 'always',
  });
  const [proposal, setProposal] = useState(initialProposal);
  const [targetApp, setTargetApp] = useState<number>();
  const [targetArchive, setTargetArchive] = useState<number>();
  const [appliedDetail, setAppliedDetail] = useState('');
  const [outcomeChoice, setOutcomeChoice] = useState<number>();
  const [decision, setDecision] = useState<StrategyDecision>();
  const [pending, setPending] = useState<StrategyCommand>();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const inFlight = useRef(false);
  const change = status.isSuccess ? status.data.change : null;
  const isPickingTarget = change?.revision === 0;
  const apps = useQuery({
    queryKey: ['application-strategy-candidate-apps', applicationId, outcomeId], queryFn: () => listApplications(),
    enabled: isPickingTarget, retry: false, staleTime: 0, refetchOnMount: 'always',
  });
  const archives = useQuery({
    queryKey: ['application-strategy-target-archives', applicationId, outcomeId, targetApp],
    queryFn: ({ signal }) => listStrategyArchives(applicationId, outcomeId, targetApp!, signal),
    enabled: isPickingTarget && !!targetApp, retry: false, staleTime: 0, refetchOnMount: 'always',
  });

  async function submit(input: StrategyCommand) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPending(input);
    setBusy(true);
    setFailure('');
    try {
      // Older GETs must not overwrite a committed response in the same cache.
      await queryClient.cancelQueries({ queryKey: statusKey, exact: true });
      const saved = await writeStrategy(applicationId, outcomeId, input);
      queryClient.setQueryData(statusKey, saved);
      setPending(undefined);
    } catch (error) {
      const code = isAxiosError(error) ? error.response?.status : undefined;
      if (!code || code >= 500) {
        setFailure(t("还不能确认是否已保存。重试会沿用本次请求，不会重复写入。"));
      } else {
        setPending(undefined);
        setFailure(t("这次未保存。请核对最新记录后再确认。"));
        await queryClient.invalidateQueries({ queryKey: statusKey, exact: true });
      }
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  function start(action: StrategyAction) {
    if (!pending && !status.isFetching) void submit({ ...action, idempotency_key: crypto.randomUUID() });
  }

  const disabled = busy || !!pending || status.isFetching;
  const labels = decisionLabels();
  return <section className={styles.strategy} aria-label={t("投递改法跟进")}>
    <h4>{t("把改法带到下一次投递")}</h4>
    {status.isPending || (status.isFetching && !pending) ? <Spin size="small" /> : null}
    {status.isError ? <Alert type="warning" showIcon message={t("来源或记录暂不可用。")}
      action={<Button size="small" onClick={() => void status.refetch()}>{t("重新读取")}</Button>} /> : null}
    {status.isSuccess && (!status.isFetching || pending) ? <>
      <p>{sourceLabel} · {resultLabel(status.data.origin, timeZone)}</p>
      {!status.data.origin.has_external_feedback ? <p>{t("未记录外部反馈，不判断未通过的原因。")}</p> : null}
      {change ? <p>{t("这次要试的改法：")}{change.proposal}</p> : <>
        <Input.TextArea aria-label={t("下一次的具体改法")} rows={2} value={proposal} maxLength={500} disabled={disabled} onChange={e => setProposal(e.target.value)} />
        <Button type="primary" onClick={() => start({ action: 'propose', proposal })} disabled={disabled || !proposal.trim()}>{t("保存改法")}</Button>
      </>}
      {isPickingTarget ? <>
        <p>{t("只有已在某份投递实际使用，才确认归档。")}</p>
        <Select aria-label={t("使用改法的岗位")} placeholder={t("选择另一份投递")} value={targetApp} disabled={disabled || apps.isFetching}
          onChange={(id: number) => { setTargetApp(id); setTargetArchive(undefined); }}
          options={(apps.data ?? []).filter(app => app.id !== applicationId).map(app => ({
            value: app.id, label: `${app.company_name} · ${app.position_name} · #${app.id}`,
          }))} />
        {apps.isError ? <Button onClick={() => void apps.refetch()}>{t("重新读取岗位")}</Button> : null}
        {targetApp ? <Select aria-label={t("实际使用的投递档案")} placeholder={t("选择确切投递档案")} value={targetArchive}
          disabled={disabled || archives.isFetching} loading={archives.isFetching}
          notFoundContent={archives.isError ? t("档案暂不可用") : t("这个岗位暂无符合条件的投递档案")}
          onChange={(id: number) => setTargetArchive(id)}
          options={(archives.data ?? []).map(item => ({
            value: item.id,
            label: `${applicationEvidenceTime(item.submitted_at, timeZone, 'archive_utc') ?? t("时间不可用")} · ${item.resume_title} · JD v${item.jd_version_number} · ${t("档案 #")}${item.id}`,
          }))} /> : null}
        <Input.TextArea aria-label={t("实际用上的改动")} rows={2} value={appliedDetail} maxLength={500} disabled={disabled} onChange={e => setAppliedDetail(e.target.value)} />
        <Button type="primary" disabled={disabled || !targetApp || !targetArchive || archives.isFetching || archives.isError
          || !archives.data?.some(item => item.id === targetArchive) || !appliedDetail.trim()}
          onClick={() => {
            if (targetApp && targetArchive) start({ action: 'confirm_applied', expected_revision: 0, target_application_id: targetApp, target_snapshot_id: targetArchive, applied_detail: appliedDetail });
          }}>{t("确认已用在这份档案")}</Button>
      </> : null}
      {change && status.data.target ? <>
        <p>{status.data.target.company_name} · {status.data.target.position_name} · {t("档案 #")}{status.data.target.id}
          {' · '}{applicationEvidenceTime(status.data.target.submitted_at, timeZone, 'archive_utc') ?? t("时间不可用")}</p>
        <p>{t("实际改动：")}{change.applied_detail}</p>
        {change.revision === 1 && !status.data.comparable_outcomes.length
          ? <Space wrap><span>{t("等待这份档案的初筛结果")}</span><Button size="small" disabled={disabled} onClick={() => void status.refetch()}>{t("刷新结果")}</Button></Space> : null}
        {change.revision === 1 && status.data.comparable_outcomes.length ? <>
          <p>{t("只比较已记录的结果，不能证明改法造成了差异。")}</p>
          <Select aria-label={t("用于比较的初筛结果")} placeholder={t("选择确切结果")} value={outcomeChoice} disabled={disabled}
            onChange={(id: number) => setOutcomeChoice(id)}
            options={status.data.comparable_outcomes.map(item => ({ value: item.id, label: resultLabel(item, timeZone) }))} />
          <Select aria-label={t("下次是否沿用改法")} placeholder={t("选择你的决定")} value={decision} disabled={disabled}
            onChange={(item: StrategyDecision) => setDecision(item)}
            options={Object.entries(labels).map(([value, label]) => ({ value, label }))} />
          <Button type="primary" disabled={disabled || !decision || !status.data.comparable_outcomes.some(item => item.id === outcomeChoice)}
            onClick={() => {
              if (decision && outcomeChoice) start({ action: 'decide', expected_revision: 1, target_outcome_id: outcomeChoice, decision });
            }}>{t("确认本次决定")}</Button>
        </> : null}
        {change.revision === 2 && change.decision ? <>
          <p>{t("这一次的决定：")}{labels[change.decision]}</p>
          {status.data.comparable_outcomes.map(item => <p key={item.id}>{resultLabel(item, timeZone)}</p>)}
        </> : null}
      </> : null}
    </> : null}
    {failure ? <Alert type="warning" showIcon message={failure} /> : null}
    {pending && !busy ? <Button onClick={() => void submit(pending)}>{t("重试同一请求")}</Button> : null}
  </section>;
}
