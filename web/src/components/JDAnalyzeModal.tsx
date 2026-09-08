import { useState } from 'react';
import { Modal, Input, Button, message, Spin, Tag, Divider } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import { analyzeJD } from '@/services/ai';
import type { Application } from '@/types/application';
import type { AnalyzeJDResponse } from '@/types/ai';

interface JDAnalyzeModalProps {
  open: boolean;
  application: Application | null;
  onClose: () => void;
}

const LABEL_STYLE = { color: '#64748b', fontWeight: 600, marginBottom: 4 };

export default function JDAnalyzeModal({ open, application, onClose }: JDAnalyzeModalProps) {
  const [jdText, setJdText] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AnalyzeJDResponse | null>(null);

  const handleAnalyze = async () => {
    if (!application) return;
    if (!jdText.trim()) {
      message.warning('请粘贴 JD 文本');
      return;
    }
    setLoading(true);
    try {
      const res = await analyzeJD({ jd_text: jdText, application_id: application.id });
      setResult(res);
      message.success('分析完成');
    } catch (e: any) {
      message.error(e?.response?.data?.error ?? '分析失败');
    } finally {
      setLoading(false);
    }
  };

  const close = () => {
    setJdText('');
    setResult(null);
    onClose();
  };

  return (
    <Modal
      title="JD 智能分析"
      open={open}
      onCancel={close}
      width={640}
      footer={
        result
          ? [<Button key="close" onClick={close}>关闭</Button>]
          : [
              <Button key="cancel" onClick={close}>取消</Button>,
              <Button
                key="ok"
                type="primary"
                icon={<RobotOutlined />}
                loading={loading}
                onClick={handleAnalyze}
              >
                开始分析
              </Button>,
            ]
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin tip="AI 分析中…" />
        </div>
      ) : result ? (
        <AnalysisView result={result.result} />
      ) : (
        <>
          <p style={LABEL_STYLE}>粘贴 JD 文本进行分析：</p>
          <Input.TextArea
            rows={8}
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            placeholder="复制招聘 JD 全文粘贴到这里…"
          />
          <p style={{ marginTop: 8, color: '#94a3b8', fontSize: 12 }}>
            分析结果会保存到本地，并关联到「{application?.company_name} · {application?.position_name}」。
          </p>
        </>
      )}
    </Modal>
  );
}

function AnalysisView({ result }: { result: AnalyzeJDResponse['result'] }) {
  return (
    <div>
      <p style={LABEL_STYLE}>摘要</p>
      <p>{result.summary}</p>

      <div style={{ display: 'flex', gap: 16, marginBottom: 8 }}>
        <span><Tag color="blue">年限 {result.experience_years}</Tag></span>
        <span><Tag color="purple">学历 {result.education}</Tag></span>
      </div>

      {result.tech_stack.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL_STYLE}>技术栈</p>
          <div>
            {result.tech_stack.map((t) => (
              <Tag key={t} color="cyan">{t}</Tag>
            ))}
          </div>
        </>
      )}

      {result.requirements.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL_STYLE}>关键要求</p>
          <ul>
            {result.requirements.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </>
      )}

      {result.highlights.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL_STYLE}>亮点</p>
          <ul>
            {result.highlights.map((h) => <li key={h}>{h}</li>)}
          </ul>
        </>
      )}

      {result.suggestions.length > 0 && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <p style={LABEL_STYLE}>准备建议</p>
          <ul>
            {result.suggestions.map((s) => <li key={s}>{s}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}