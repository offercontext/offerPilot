import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import {
  Modal,
  Input,
  Button,
  Select,
  message,
  Spin,
  Progress,
  Divider,
  Empty,
} from 'antd';
import { RobotOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons';
import { listResumes, createResume, matchResume, uploadResume } from '@/services/resumes';
import type { MatchResumeResponse } from '@/types/resume';
import ResumeUploadModal from './ResumeUploadModal';
import workflowStyles from './ui/WorkflowSurface.module.css';

interface ResumeMatchModalProps {
  open: boolean;
  onClose: () => void;
}

const LABEL = { color: '#64748b', fontWeight: 600, marginBottom: 4 };

export default function ResumeMatchModal({ open, onClose }: ResumeMatchModalProps) {
  const [selectedResume, setSelectedResume] = useState<number | undefined>();
  const [jdText, setJdText] = useState('');
  const [match, setMatch] = useState<MatchResumeResponse | null>(null);

  // Add-resume form state
  const [showAdd, setShowAdd] = useState(false);
  const [newName, setNewName] = useState('');
  const [newText, setNewText] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);

  const resumesQuery = useQuery({
    queryKey: ['resumes'],
    queryFn: listResumes,
    enabled: open,
  });

  const useUploadMut = useMutation({
    mutationFn: (file: File) => uploadResume(file),
    onSuccess: (res) => {
      message.success(res.parse_status === 'text-ready' ? '简历已上传' : '已上传，文本提取失败，请到简历库校正');
      resumesQuery.refetch();
      setUploadOpen(false);
    },
    onError: () => message.error('上传失败'),
  });

  const addResumeMut = useMutation({
    mutationFn: () => createResume({ name: newName, text: newText }),
    onSuccess: () => {
      message.success('简历已保存');
      setNewName('');
      setNewText('');
      setShowAdd(false);
      resumesQuery.refetch();
    },
    onError: () => message.error('保存失败'),
  });

  const matchMut = useMutation({
    mutationFn: () => matchResume(selectedResume!, { jd_text: jdText }),
    onSuccess: (res) => {
      setMatch(res);
      message.success('匹配完成');
    },
    onError: (e: any) => message.error(e?.response?.data?.error ?? '匹配失败'),
  });

  const close = () => {
    setSelectedResume(undefined);
    setJdText('');
    setMatch(null);
    setShowAdd(false);
    onClose();
  };

  const resumeOptions = (resumesQuery.data ?? []).map((r) => ({
    value: r.id,
    label: r.name || `简历 #${r.id}`,
  }));

  return (
    <>
    <Modal
      title="简历匹配度检查"
      open={open}
      onCancel={close}
      width={640}
      footer={
        match ? (
          [<Button key="close" onClick={close}>关闭</Button>]
        ) : (
          [
            <Button key="cancel" onClick={close}>取消</Button>,
            <Button
              key="ok"
              type="primary"
              icon={<RobotOutlined />}
              loading={matchMut.isPending}
              disabled={!selectedResume || !jdText.trim()}
              onClick={() => matchMut.mutate()}
            >
              匹配分析
            </Button>,
          ]
        )
      }
    >
      <div data-testid="resume-match-result-surface" className={`${workflowStyles.surface} ${workflowStyles.stack}`}>
      {matchMut.isPending ? (
        <div className="op-empty-state">
          <Spin tip="AI 匹配中…" />
        </div>
      ) : match ? (
        <MatchView match={match} />
      ) : (
        <>
          <div className="op-section-heading">
            <p style={{ ...LABEL, margin: 0 }}>选择简历</p>
            <div className={workflowStyles.actionGroup}>
            <Button
              size="small"
              type="link"
              icon={<PlusOutlined />}
              onClick={() => setShowAdd((v) => !v)}
            >
              {showAdd ? '取消' : '粘贴文本'}
            </Button>
            <Button
              size="small"
              type="link"
              icon={<UploadOutlined />}
              onClick={() => setUploadOpen(true)}
            >
              上传 PDF
            </Button>
            </div>
          </div>
          <Select
            style={{ width: '100%' }}
            placeholder="选择已保存的简历"
            value={selectedResume}
            onChange={setSelectedResume}
            options={resumeOptions}
            loading={resumesQuery.isLoading}
            notFoundContent={<Empty description="还没有简历，点击「添加简历」" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
          />

          {showAdd && (
            <div className={workflowStyles.section} style={{ marginTop: 12 }}>
              <Input
                placeholder="简历名称（可选）"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                style={{ marginBottom: 8 }}
              />
              <Input.TextArea
                rows={5}
                placeholder="粘贴简历文本…"
                value={newText}
                onChange={(e) => setNewText(e.target.value)}
              />
              <div className={workflowStyles.actionGroup} style={{ marginTop: 8 }}><Button
                type="primary"
                size="small"
                loading={addResumeMut.isPending}
                disabled={!newText.trim()}
                onClick={() => addResumeMut.mutate()}
              >
                保存简历
              </Button></div>
            </div>
          )}

          <Divider style={{ margin: '12px 0' }} />
          <p style={LABEL}>粘贴目标 JD 文本</p>
          <Input.TextArea
            rows={6}
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            placeholder="复制招聘 JD 全文…"
          />
        </>
      )}
      </div>
    </Modal>
    <ResumeUploadModal
      open={uploadOpen}
      uploading={useUploadMut.isPending}
      onSubmit={(f) => useUploadMut.mutate(f)}
      onClose={() => setUploadOpen(false)}
    />
    </>
  );
}

function MatchView({ match }: { match: MatchResumeResponse }) {
  const r = match.result;
  const color = r.match_score >= 70 ? '#16a34a' : r.match_score >= 40 ? '#ea580c' : '#dc2626';
  return (
    <div className={`${workflowStyles.stack} op-long-text`}>
      <div style={{ textAlign: 'center', marginBottom: 12 }}>
        <Progress type="circle" percent={r.match_score} strokeColor={color} />
      </div>
      <p style={LABEL}>总评</p>
      <div className={workflowStyles.evidenceBlock}><p>{r.summary}</p></div>

      {r.matched.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL}>✅ 匹配点</p>
          <ul className={workflowStyles.evidenceBlock}>
            {r.matched.map((m) => <li key={m}>{m}</li>)}
          </ul>
        </>
      )}

      {r.gaps.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL}>⚠️ 差距</p>
          <ul className={workflowStyles.evidenceBlock}>
            {r.gaps.map((g) => <li key={g}>{g}</li>)}
          </ul>
        </>
      )}

      {r.suggestions.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL}>💡 优化建议</p>
          <ul className={workflowStyles.evidenceBlock}>
            {r.suggestions.map((s) => <li key={s}>{s}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}
