import { liveText } from '@/i18n/liveText';
import { useLocalizedValidation } from '@/components/applicationUiText';
import { t, useLocale } from '@/i18n';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Skeleton,
  Space,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  BulbOutlined,
  DeleteOutlined,
  EditOutlined,
  FireOutlined,
  PlusOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  createQuestion,
  deleteQuestion,
  generateQuestions,
  getPracticeStats,
  listDueQuestions,
  listQuestions,
  submitReview,
  updateQuestion,
} from '@/services/questions';
import type {
  Question,
  QuestionDifficulty,
  QuestionInput,
  QuestionStatus,
  ReviewRating,
} from '@/types/question';
import styles from './QuestionBankView.module.css';

const { Paragraph } = Typography;

const DIFFICULTY_META: Record<QuestionDifficulty, { label: string; color: string }> = {
  easy: { get label() { return t("简单"); }, color: 'green' },
  medium: { get label() { return t("中等"); }, color: 'gold' },
  hard: { get label() { return t("困难"); }, color: 'red' },
};

const STATUS_META: Record<QuestionStatus, { label: string; color: string }> = {
  new: { get label() { return t("未刷"); }, color: 'default' },
  practicing: { get label() { return t("练习中"); }, color: 'blue' },
  mastered: { get label() { return t("已掌握"); }, color: 'green' },
};

interface QuestionBankViewProps {
  focusId?: number;
  practiceRequestToken?: number;
}

export default function QuestionBankView({
  focusId,
  practiceRequestToken,
}: QuestionBankViewProps) {
  useLocale();
  const [tab, setTab] = useState<'question_bank' | 'review'>(practiceRequestToken ? 'review' : 'question_bank');
  const lastPracticeRequestTokenRef = useRef<number | undefined>(practiceRequestToken);

  // When launched from a mock-interview drill link, surface the target id.
  useEffect(() => {
    if (focusId) {
      message.info(liveText(() => t("正在定位题目 #{questionId}，可在题库中按 ID 查找", { questionId: focusId })));
    }
  }, [focusId]);

  useEffect(() => {
    const previous = lastPracticeRequestTokenRef.current;
    lastPracticeRequestTokenRef.current = practiceRequestToken;
    if (practiceRequestToken !== undefined && practiceRequestToken > 0 && practiceRequestToken > (previous ?? 0)) {
      setTab('review');
    }
  }, [practiceRequestToken]);

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>{t("题库刷题")}</h1>
          <p className={styles.subtitle}>{t("基于已记录的面试问题生成题目，管理题库，按间隔复习巩固面试知识")}</p>
        </div>
        <Segmented
          value={tab}
          onChange={(v) => setTab(v as 'question_bank' | 'review')}
          options={[
            { label: t("题库"), value: 'question_bank' },
            { label: t("今日复习"), value: 'review' },
          ]}
        />
      </div>

      <section hidden={tab !== 'question_bank'} aria-label={t("题库模式")}><BankTab /></section>
      <section hidden={tab !== 'review'} aria-label={t("今日复习模式")}><PracticeTab /></section>
    </div>
  );
}

/* ----------------------------- 题库 tab ----------------------------- */

function BankTab() {
  const locale = useLocale();
  const qc = useQueryClient();
  const [status, setStatus] = useState<QuestionStatus | 'all'>('all');
  const [difficulty, setDifficulty] = useState<QuestionDifficulty | 'all'>('all');
  const [search, setSearch] = useState('');
  const [generateOpen, setGenerateOpen] = useState(false);
  const [editing, setEditing] = useState<Question | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const filter = useMemo(
    () => ({
      ...(status !== 'all' ? { status } : {}),
      ...(difficulty !== 'all' ? { difficulty } : {}),
    }),
    [status, difficulty],
  );

  const { data: questions = [], isLoading } = useQuery({
    queryKey: ['questions', filter],
    queryFn: () => listQuestions(filter),
  });

  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return questions;
    return questions.filter(
      (q) =>
        q.question.toLowerCase().includes(term) ||
        q.category.toLowerCase().includes(term) ||
        q.tags.some((t) => t.toLowerCase().includes(term)),
    );
  }, [questions, search]);

  const delMutation = useMutation({
    mutationFn: (id: number) => deleteQuestion(id),
    onSuccess: () => {
      message.success(liveText(() => t("已删除")));
      qc.invalidateQueries({ queryKey: ['questions'] });
      qc.invalidateQueries({ queryKey: ['questions-due'] });
    },
    onError: () => message.error(liveText(() => t("删除失败"))),
  });

  const toggleExpand = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  if (generateOpen) {
    return <GenerateDrawer open={generateOpen} onClose={() => setGenerateOpen(false)} />;
  }

  return (
    <>
      <div className={styles.toolbar}>
        <Button
          type="primary"
          icon={<ThunderboltOutlined />}
          className="op-ai-btn"
          onClick={() => setGenerateOpen(true)}
        >
          {t("AI 生成题目")}</Button>
        <Button icon={<PlusOutlined />} onClick={() => setManualOpen(true)}>
          {t("手动添加")}</Button>
        <span className={styles.spacer} />
        <Input.Search
          placeholder={t("搜索题目 / 分类 / 标签")}
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ width: locale === 'en-US' ? 340 : 240, maxWidth: '100%' }}
        />
        <Select
          value={status}
          onChange={setStatus}
          style={{ width: locale === 'en-US' ? 145 : 120 }}
          aria-label={t("按状态筛选")}
          options={[
            { label: t("全部状态"), value: 'all' },
            { label: t("未刷"), value: 'new' },
            { label: t("练习中"), value: 'practicing' },
            { label: t("已掌握"), value: 'mastered' },
          ]}
        />
        <Select
          value={difficulty}
          onChange={setDifficulty}
          style={{ width: locale === 'en-US' ? 150 : 110 }}
          aria-label={t("按难度筛选")}
          options={[
            { label: t("全部难度"), value: 'all' },
            { label: t("简单"), value: 'easy' },
            { label: t("中等"), value: 'medium' },
            { label: t("困难"), value: 'hard' },
          ]}
        />
      </div>

      {isLoading ? (
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          {[0, 1, 2].map((i) => (
            <div key={i} className={styles.qCard}>
              <Skeleton active paragraph={{ rows: 2 }} title={false} />
            </div>
          ))}
        </Space>
      ) : visible.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            questions.length === 0 ? t("题库还是空的") : t("没有符合条件的题目")
          }
          style={{ padding: '48px 0' }}
        >
          {questions.length === 0 && (
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              className="op-ai-btn"
              onClick={() => setGenerateOpen(true)}
            >
              {t("从面试复盘生成题目，或手动添加")}</Button>
          )}
        </Empty>
      ) : (
        <div className={styles.qList}>
          {visible.map((q) => (
            <div key={q.id} className={styles.qCard}>
              <div className={styles.qMeta}>
                {q.category && <Tag color="purple">{q.category}</Tag>}
                <Tag color={DIFFICULTY_META[q.difficulty].color}>
                  {DIFFICULTY_META[q.difficulty].label}
                </Tag>
                <Tag color={STATUS_META[q.status].color}>{STATUS_META[q.status].label}</Tag>
                {q.practice_count > 0 && (
                  <span style={{ fontSize: 12, color: 'var(--op-muted)' }}>
                    {t("刷题 {count} 次", { count: q.practice_count })}</span>
                )}
                <div className={styles.qActions}>
                  <Tooltip title={t("编辑")}>
                    <Button
                      type="text"
                      size="small"
                      aria-label={t("编辑题目")}
                      icon={<EditOutlined />}
                      onClick={() => setEditing(q)}
                    />
                  </Tooltip>
                  <Popconfirm
                    title={t("删除这道题？")}
                    okText={t("删除")}
                    cancelText={t("取消")}
                    okButtonProps={{ danger: true }}
                    onConfirm={() => delMutation.mutate(q.id)}
                  >
                    <Tooltip title={t("删除")}>
                      <Button type="text" size="small" aria-label={t("删除题目")} icon={<DeleteOutlined />} danger />
                    </Tooltip>
                  </Popconfirm>
                </div>
              </div>
              <div
                className={styles.qStem}
                onClick={() => toggleExpand(q.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggleExpand(q.id);
                  }
                }}
              >
                {q.question}
              </div>
              {expanded.has(q.id) && q.reference_answer && (
                <div className={styles.qAnswer}>{q.reference_answer}</div>
              )}
              {expanded.has(q.id) && !q.reference_answer && (
                <div className={styles.qAnswer} style={{ color: 'var(--op-muted)' }}>
                  {t("（暂无参考答案）")}</div>
              )}
            </div>
          ))}
        </div>
      )}

      <QuestionFormModal
        open={manualOpen || !!editing}
        question={editing}
        onClose={() => {
          setManualOpen(false);
          setEditing(null);
        }}
      />
    </>
  );
}

/* --------------------------- 生成抽屉 --------------------------- */

function GenerateDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  useLocale();
  const qc = useQueryClient();
  const [source, setSource] = useState<'knowledge' | 'notes'>('notes');
  const [count, setCount] = useState(8);

  const genMutation = useMutation({
    mutationFn: () =>
      generateQuestions({
        source,
        count,
      }),
    onSuccess: (res) => {
      message.success(liveText(() => t("已生成 {count} 道题{duplicates}", { count: res.count, duplicates: res.skipped > 0 ? t("，跳过 {count} 道重复", { count: res.skipped }) : '' })));
      qc.invalidateQueries({ queryKey: ['questions'] });
      qc.invalidateQueries({ queryKey: ['questions-due'] });
      onClose();
    },
    onError: (err: unknown) => {
      const serverError = (err as { response?: { data?: { error?: string } } })?.response?.data?.error;
      message.error(serverError ?? liveText(() => t("生成失败，请检查 AI 配置")));
    },
  });

  if (!open) return null;

  return (
    <section aria-label={t("AI 生成题目")}>
      <div className={styles.layerHeader}>
        <div>
          <Button type="link" className={styles.backButton} onClick={onClose}>
            {t("返回题库")}</Button>
          <h2 className={styles.layerTitle}>{t("AI 生成题目")}</h2>
        </div>
      </div>
      <Space direction="vertical" size={20} style={{ width: '100%' }}>
        <div>
          <div style={{ marginBottom: 8, fontWeight: 600 }}>{t("来源")}</div>
          <Segmented
            block
            value={source}
            onChange={(v) => setSource(v as 'knowledge' | 'notes')}
            options={[
              { label: t("参考资料（暂未开放）"), value: 'knowledge', disabled: true },
              { label: t("面试复盘真题"), value: 'notes' },
            ]}
          />
        </div>

        {source === 'knowledge' && (
          <Paragraph type="secondary" style={{ margin: 0 }}>
            {t("参考资料出题暂未开放，可选择面试复盘或手动添加题目。")}</Paragraph>
        )}

        {source === 'notes' && (
          <Paragraph type="secondary" style={{ margin: 0 }}>
            {t("将从已记录的面试问题中提炼题目。参考资料出题暂未开放，可先手动添加题目。")}</Paragraph>
        )}

        <div>
          <div style={{ marginBottom: 8, fontWeight: 600 }}>{t("生成数量")}</div>
          <InputNumber min={1} max={20} value={count} onChange={(v) => setCount(v ?? 8)} style={{ width: '100%' }} />
        </div>

        <Button
          type="primary"
          block
          className="op-ai-btn"
          icon={<ThunderboltOutlined />}
          loading={genMutation.isPending}
          onClick={() => genMutation.mutate()}
        >
          {genMutation.isPending ? t("AI 正在出题…") : t("开始生成")}
        </Button>
        <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
          {t("已存在的题目会自动去重（精确 + 近似），不会重复入库。")}</Paragraph>
      </Space>
    </section>
  );
}

/* --------------------------- 手动增/改 --------------------------- */

function QuestionFormModal({
  open,
  question,
  onClose,
}: {
  open: boolean;
  question: Question | null;
  onClose: () => void;
}) {
  useLocale();
  const qc = useQueryClient();
  const [form] = Form.useForm();
  useLocalizedValidation(form);
  const isEdit = !!question;

  useEffect(() => {
    if (open) {
      form.setFieldsValue({
        question: question?.question ?? '',
        category: question?.category ?? '',
        difficulty: question?.difficulty ?? 'medium',
        reference_answer: question?.reference_answer ?? '',
        tags: question?.tags ?? [],
      });
    }
  }, [open, question, form]);

  const saveMutation = useMutation({
    mutationFn: (values: QuestionInput) =>
      isEdit ? updateQuestion(question!.id, values) : createQuestion(values),
    onSuccess: () => {
      message.success(isEdit ? t("已保存") : t("已添加"));
      qc.invalidateQueries({ queryKey: ['questions'] });
      qc.invalidateQueries({ queryKey: ['questions-due'] });
      onClose();
    },
    onError: () => message.error(liveText(() => t("保存失败"))),
  });

  return (
    <Modal
      title={isEdit ? t("编辑题目") : t("手动添加题目")}
      open={open}
      onCancel={onClose}
      onOk={() => form.submit()}
      okText={t("保存")}
      cancelText={t("取消")}
      confirmLoading={saveMutation.isPending}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) => saveMutation.mutate(values as QuestionInput)}
        style={{ marginTop: 12 }}
      >
        <Form.Item
          name="question"
          label={t("题目")}
          rules={[{ required: true, message: t("请输入题目") }]}
        >
          <Input.TextArea rows={2} placeholder={t("例如：什么是 goroutine 泄漏？如何排查？")} />
        </Form.Item>
        <Space size={12} style={{ display: 'flex' }}>
          <Form.Item name="category" label={t("分类")} style={{ flex: 1 }}>
            <Input placeholder={t("如 Go并发 / 系统设计")} />
          </Form.Item>
          <Form.Item name="difficulty" label={t("难度")} style={{ width: 120 }}>
            <Select
              options={[
                { label: t("简单"), value: 'easy' },
                { label: t("中等"), value: 'medium' },
                { label: t("困难"), value: 'hard' },
              ]}
            />
          </Form.Item>
        </Space>
        <Form.Item name="tags" label={t("标签")}>
          <Select mode="tags" placeholder={t("回车添加标签")} tokenSeparators={[',']} />
        </Form.Item>
        <Form.Item name="reference_answer" label={t("参考答案")}>
          <Input.TextArea rows={4} placeholder={t("要点式参考答案")} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/* ----------------------------- 刷题打卡 tab ----------------------------- */

function PracticeTab() {
  useLocale();
  const qc = useQueryClient();
  const [revealed, setRevealed] = useState(false);
  const [doneToday, setDoneToday] = useState(0);

  const { data: stats } = useQuery({
    queryKey: ['questions', 'stats'],
    queryFn: () => getPracticeStats(),
  });
  const { data: due = [], isLoading } = useQuery({
    queryKey: ['questions-due'],
    queryFn: () => listDueQuestions(40),
  });

  const current = due[0];

  const reviewMutation = useMutation({
    mutationFn: ({ id, rating }: { id: number; rating: ReviewRating }) => submitReview(id, rating),
    onSuccess: () => {
      setRevealed(false);
      setDoneToday((n) => n + 1);
      qc.invalidateQueries({ queryKey: ['questions-due'] });
      qc.invalidateQueries({ queryKey: ['questions'] });
    },
    onError: () => message.error(liveText(() => t("打卡失败"))),
  });

  const rate = (rating: ReviewRating) => {
    if (!current || reviewMutation.isPending) return;
    reviewMutation.mutate({ id: current.id, rating });
  };

  // Keyboard shortcuts: space reveals, 1/2/3 rate.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return;
      if (!current) return;
      if (!revealed && (e.key === ' ' || e.key === 'Enter')) {
        e.preventDefault();
        setRevealed(true);
      } else if (revealed && (e.key === '1' || e.key === '2' || e.key === '3')) {
        e.preventDefault();
        rate(Number(e.key) as ReviewRating);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const sessionTotal = doneToday + due.length;
  const progress = sessionTotal > 0 ? Math.round((doneToday / sessionTotal) * 100) : 0;
  const masteryRate =
    stats && stats.total > 0 ? Math.round((stats.mastered / stats.total) * 100) : 0;

  return (
    <>
      <div className={styles.statRow}>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>
            <ThunderboltOutlined /> {t("今日已刷")}</span>
          <span className={`${styles.statValue} op-tnum`}>{stats?.today_reviews ?? 0}</span>
          <span className={styles.statHint}>{t("本次会话 {count} 道", { count: doneToday })}</span>
        </div>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>
            <FireOutlined /> {t("连续打卡")}</span>
          <span className={`${styles.statValue} op-tnum`}>{t("{count} 天", { count: stats?.streak_days ?? 0 })}</span>
          <span className={styles.statHint}>{t("坚持就是胜利")}</span>
        </div>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>
            <BulbOutlined /> {t("已掌握")}</span>
          <span className={`${styles.statValue} op-tnum`}>{masteryRate}%</span>
          <span className={styles.statHint}>
            {t("{mastered} / {total} 道", { mastered: stats?.mastered ?? 0, total: stats?.total ?? 0 })}</span>
        </div>
        <div className={styles.statCard}>
          <span className={styles.statLabel}>
            <ReloadOutlined /> {t("待复习")}</span>
          <span className={`${styles.statValue} op-tnum`}>{stats?.due ?? 0}</span>
          <span className={styles.statHint}>{t("到期需要巩固")}</span>
        </div>
      </div>

      {isLoading ? (
        <div className={styles.flash}>
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
      ) : !current ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            (stats?.total ?? 0) === 0 ? t("题库还是空的，先去生成一些题目吧") : t("太棒了，今天的复习队列已清空")
          }
          style={{ padding: '56px 0' }}
        />
      ) : (
        <div className={styles.flash}>
          <div className={styles.qMeta}>
            {current.category && <Tag color="purple">{current.category}</Tag>}
            <Tag color={DIFFICULTY_META[current.difficulty].color}>
              {DIFFICULTY_META[current.difficulty].label}
            </Tag>
            <Tag color={STATUS_META[current.status].color}>{STATUS_META[current.status].label}</Tag>
          </div>

          <div className={styles.flashStem}>{current.question}</div>

          {revealed ? (
            <div className={styles.flashAnswer}>
              {current.reference_answer || t("（这道题暂无参考答案，凭记忆自评即可）")}
            </div>
          ) : (
            <Button size="large" onClick={() => setRevealed(true)} style={{ alignSelf: 'flex-start' }}>
              {t("显示答案")}{" "}<span className={styles.rateKey}>{t("（空格）")}</span>
            </Button>
          )}

          <div className={styles.flashFooter}>
            <div className={styles.progressBar} aria-hidden>
              <div className={styles.progressFill} style={{ width: `${progress}%` }} />
            </div>
            {revealed && (
              <div className={styles.rateRow}>
                <button
                  className={`${styles.rateBtn} ${styles.rateAgain}`}
                  onClick={() => rate(1)}
                  disabled={reviewMutation.isPending}
                >
                  {t("不会")}<span className={styles.rateKey}>{t("1 · 明天再练")}</span>
                </button>
                <button
                  className={`${styles.rateBtn} ${styles.rateHard}`}
                  onClick={() => rate(2)}
                  disabled={reviewMutation.isPending}
                >
                  {t("模糊")}<span className={styles.rateKey}>{t("2 · 3 天后")}</span>
                </button>
                <button
                  className={`${styles.rateBtn} ${styles.rateGood}`}
                  onClick={() => rate(3)}
                  disabled={reviewMutation.isPending}
                >
                  {t("掌握")}<span className={styles.rateKey}>{t("3 · 7 天后")}</span>
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
