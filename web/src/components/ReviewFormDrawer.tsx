import { useEffect, useState } from 'react';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button, Form, Input, Select, Space } from 'antd';
import type { Application } from '@/types/application';
import type { CreateNoteInput, InterviewNote } from '@/types/note';
import type { ScheduleEvent } from '@/types/event';
import { listEvents } from '@/services/events';

const MOOD_OPTIONS = [
  { value: 'good', label: '好' },
  { value: 'normal', label: '一般' },
  { value: 'bad', label: '差' },
];

interface Props {
  open: boolean;
  applications: Application[];
  initialApplication?: Application | null;
  note?: InterviewNote | null;
  initialEventID?: number | null;
  saving?: boolean;
  onSubmit: (input: CreateNoteInput) => void;
  onClose: () => void;
}

export default function ReviewFormDrawer({
  open,
  applications,
  initialApplication,
  note,
  initialEventID,
  saving = false,
  onSubmit,
  onClose,
}: Props) {
  const [form] = Form.useForm<CreateNoteInput>();
  const [interviewEvents, setInterviewEvents] = useState<ScheduleEvent[]>([]);
  const editing = !!note;

  async function loadInterviewEvents(applicationID?: number) {
    if (!applicationID) {
      setInterviewEvents([]);
      return;
    }
    try {
      const events = await listEvents({ application_id: applicationID, event_type: 'interview' });
      setInterviewEvents(events.filter((event) => event.event_type === 'interview'));
    } catch {
      setInterviewEvents([]);
    }
  }

  useEffect(() => {
    if (!open) return;

    if (note) {
      form.setFieldsValue({
        application_id: note.application_id,
        application_event_id: note.application_event_id ?? undefined,
        company: note.company,
        position: note.position,
        round: note.round,
        date: note.date,
        questions: note.questions,
        self_reflection: note.self_reflection,
        difficulty_points: note.difficulty_points,
        mood: note.mood,
      });
      void loadInterviewEvents(note.application_id);
      return;
    }

    form.resetFields();
    if (initialApplication) {
      form.setFieldsValue({
        application_id: initialApplication.id,
        application_event_id: initialEventID ?? undefined,
        company: initialApplication.company_name,
        position: initialApplication.position_name,
      });
      void loadInterviewEvents(initialApplication.id);
    } else {
      setInterviewEvents([]);
    }
  }, [open, note, initialApplication, initialEventID, form]);

  function handleApplicationChange(appID?: number) {
    const app = applications.find((item) => item.id === appID);
    if (!app) return;

    form.setFieldsValue({
      company: app.company_name,
      position: app.position_name,
    });
    void loadInterviewEvents(app.id);
  }

  function handleSubmit(input: CreateNoteInput) {
    if (!editing) {
      onSubmit(input);
      return;
    }

    const update: CreateNoteInput = {
      ...input,
      application_id: undefined,
      application_event_id: undefined,
    };
    delete update.application_id;
    delete update.application_event_id;

    const eventChanged = input.application_event_id !== note?.application_event_id;
    if (eventChanged && note?.application_event_id != null) {
      const confirmed = window.confirm('解除或更换绑定的面试事件？已有 AI 建议将标记为来源已变化。');
      if (!confirmed) return;
      update.application_event_id = input.application_event_id ?? null;
    } else if (eventChanged && input.application_event_id != null) {
      update.application_event_id = input.application_event_id;
    }
    onSubmit(update);
  }

  if (!open) return null;

  return (
    <section aria-label={editing ? '编辑面试复盘' : '新建面试复盘'}>
      <div style={{ display: 'grid', gap: 8, marginBottom: 18 }}>
        <Button
          type="link"
          icon={<ArrowLeftOutlined />}
          onClick={onClose}
          style={{ width: 'fit-content', height: 'auto', padding: 0 }}
        >
          返回上一层
        </Button>
        <Space style={{ width: '100%', justifyContent: 'space-between' }} align="center" wrap>
          <h2 style={{ margin: 0 }}>{editing ? '编辑面试复盘' : '新建面试复盘'}</h2>
          <Space>
            <Button onClick={onClose}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => form.submit()}>
              保存复盘
            </Button>
          </Space>
        </Space>
      </div>
      <Form form={form} layout="vertical" onFinish={handleSubmit}>
        <Form.Item name="application_id" label="关联投递">
          <Select
            allowClear
            showSearch
            placeholder="可选"
            optionFilterProp="label"
            onChange={handleApplicationChange}
            disabled={editing}
            options={applications.map((app) => ({
              value: app.id,
              label: `${app.company_name} / ${app.position_name}`,
            }))}
          />
        </Form.Item>

        <Form.Item name="application_event_id" label="绑定面试事件">
          <Select
            allowClear
            disabled={!form.getFieldValue('application_id')}
            placeholder="可选，仅支持面试事件"
            options={interviewEvents.map((event) => ({
              value: event.id,
              label: `${event.subtype || '面试'} / ${event.scheduled_at}`,
            }))}
          />
        </Form.Item>

        <Space style={{ width: '100%' }} align="start" wrap>
          <Form.Item
            name="company"
            label="公司"
            rules={[{ required: true, message: '请输入公司' }]}
            style={{ flex: '1 1 220px', minWidth: 220 }}
          >
            <Input placeholder="公司" />
          </Form.Item>
          <Form.Item name="position" label="岗位" style={{ flex: '1 1 220px', minWidth: 220 }}>
            <Input placeholder="岗位" />
          </Form.Item>
        </Space>

        <Space style={{ width: '100%' }} align="start" wrap>
          <Form.Item name="round" label="轮次" style={{ flex: '1 1 140px', minWidth: 140 }}>
            <Input placeholder="一面" />
          </Form.Item>
          <Form.Item name="date" label="日期" style={{ flex: '1 1 140px', minWidth: 140 }}>
            <Input placeholder="2026-07-01" />
          </Form.Item>
          <Form.Item name="mood" label="心情" style={{ flex: '1 1 140px', minWidth: 140 }}>
            <Select options={MOOD_OPTIONS} allowClear placeholder="选择" />
          </Form.Item>
        </Space>

        <Form.Item name="questions" label="面试问题">
          <Input.TextArea rows={4} placeholder="记录被问到的问题" />
        </Form.Item>
        <Form.Item name="self_reflection" label="自我反思">
          <Input.TextArea rows={4} placeholder="表现如何，哪里可以改进" />
        </Form.Item>
        <Form.Item name="difficulty_points" label="难点/薄弱点">
          <Input.TextArea rows={4} placeholder="哪些知识点没答好" />
        </Form.Item>
      </Form>
    </section>
  );
}
