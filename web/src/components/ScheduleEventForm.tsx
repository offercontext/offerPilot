import { useLocalizedValidation } from './applicationUiText';
import { t, useLocale } from '@/i18n';
import { useEffect, type Ref } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { Button, DatePicker, Form, Input, InputNumber, Select, Space, message } from 'antd';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import { createEvent, updateEvent } from '@/services/events';
import type { Application } from '@/types/application';
import type { ScheduleEvent, ScheduleEventInput, ScheduleEventType } from '@/types/event';
import { EVENT_TYPE_LABELS } from '@/types/event';
import workflowStyles from './ui/WorkflowSurface.module.css';

interface ScheduleEventFormProps {
  open: boolean;
  applications: Application[];
  initialApplication?: Application;
  initialDate?: string;
  event?: ScheduleEvent;
  onClose: () => void;
  headingRef?: Ref<HTMLHeadingElement>;
  onSuccess?: (event: ScheduleEvent) => void;
}

interface ScheduleEventFormValues {
  application_id: number;
  event_type: ScheduleEventType;
  subtype?: string;
  tags?: string[];
  round?: number | null;
  scheduled_at: Dayjs;
  remind_at?: Dayjs | null;
  duration_minutes: number;
  location?: string;
  notes?: string;
  status?: string;
}



function getDefaultScheduledAt() {
  return dayjs().add(1, 'day').startOf('hour');
}

export default function ScheduleEventForm({
  open,
  applications,
  initialApplication,
  initialDate,
  event,
  onClose,
  headingRef,
  onSuccess,
}: ScheduleEventFormProps) {
const EVENT_TYPE_OPTIONS = (Object.entries(EVENT_TYPE_LABELS) as [ScheduleEventType, string][]).map(
  ([value, label]) => ({ value, label })
);

  useLocale();
  const [form] = Form.useForm<ScheduleEventFormValues>();
  useLocalizedValidation(form);
  const queryClient = useQueryClient();
  const isEdit = !!event;

  const mutation = useMutation({
    mutationFn: (input: ScheduleEventInput) =>
      isEdit ? updateEvent(event.id, input) : createEvent(input),
    onSuccess: (savedEvent) => {
      message.success(isEdit ? t("日程已更新") : t("日程已创建"));
      onSuccess?.(savedEvent);
      queryClient.invalidateQueries({ queryKey: ['calendar'] });
      queryClient.invalidateQueries({ queryKey: ['events'] });
      form.resetFields();
      onClose();
    },
    onError: () => {
      message.error(isEdit ? t("更新日程失败") : t("创建日程失败"));
    },
  });

  useEffect(() => {
    if (!open) return;

    if (event) {
      form.setFieldsValue({
        application_id: event.application_id,
        event_type: event.event_type,
        subtype: event.subtype,
        tags: event.tags,
        round: event.round,
        scheduled_at: dayjs(event.scheduled_at),
        remind_at: event.remind_at ? dayjs(event.remind_at) : null,
        duration_minutes: event.duration_minutes,
        location: event.location,
        notes: event.notes,
        status: event.status,
      });
      return;
    }

    form.setFieldsValue({
      application_id: initialApplication?.id,
      event_type: 'interview',
      subtype: '',
      tags: [],
      round: 0,
      scheduled_at: initialDate ? dayjs(initialDate).hour(9).minute(0).second(0) : getDefaultScheduledAt(),
      remind_at: null,
      duration_minutes: 60,
      location: '',
      notes: '',
      status: 'todo',
    });
  }, [event, form, initialApplication, initialDate, open]);

  const handleClose = () => {
    form.resetFields();
    onClose();
  };

  const handleFinish = (values: ScheduleEventFormValues) => {
    mutation.mutate({
      application_id: values.application_id,
      event_type: values.event_type,
      subtype: values.subtype ?? '',
      tags: values.tags ?? [],
      round: values.round ?? 0,
      scheduled_at: values.scheduled_at.toISOString(),
      remind_at: values.remind_at ? values.remind_at.toISOString() : null,
      duration_minutes: values.duration_minutes,
      location: values.location ?? '',
      notes: values.notes ?? '',
      status: values.status ?? 'todo',
    });
  };

  if (!open) return null;

  return (
    <section data-testid="schedule-event-form" aria-label={isEdit ? t("编辑日程") : t("新建日程")} className={`${workflowStyles.surface} ${workflowStyles.stack}`}>
      <div className={workflowStyles.sectionHeader}>
        <div>
          <Button
            type="link"
            icon={<ArrowLeftOutlined />}
            onClick={handleClose}
            style={{ width: 'fit-content', height: 'auto', padding: 0 }}
          >{t("返回上一层")}</Button>
          <h2 ref={headingRef} tabIndex={-1} style={{ margin: '8px 0 0' }}>
            {isEdit ? t("编辑日程") : t("新建日程")}
          </h2>
        </div>
        <Space className={workflowStyles.actionGroup}>
          <Button onClick={handleClose}>{t("取消")}</Button>
          <Button type="primary" loading={mutation.isPending} onClick={() => form.submit()}>
            {isEdit ? t("保存") : t("创建")}
          </Button>
        </Space>
      </div>
      <Form form={form} layout="vertical" onFinish={handleFinish} requiredMark={false} className={workflowStyles.section}>
        <Form.Item
          name="application_id"
          label={t("投递")}
          rules={[{ required: true, message: t("请选择投递") }]}
        >
          <Select
            showSearch
            disabled={!!initialApplication || isEdit}
            placeholder={t("选择投递")}
            optionFilterProp="label"
            options={applications.map((application) => ({
              value: application.id,
              label: `${application.company_name} · ${application.position_name}`,
            }))}
          />
        </Form.Item>

        <div className={workflowStyles.formGridCompact}>
          <Form.Item
            name="event_type"
            label={t("类型")}
            rules={[{ required: true, message: t("请选择类型") }]}
          >
            <Select options={EVENT_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="round" label={t("轮次")}>
            <InputNumber min={0} precision={0} style={{ width: '100%' }} />
          </Form.Item>
        </div>

        <Form.Item name="subtype" label={t("子类型")}>
          <Input placeholder={t("例如 assessment / technical / negotiation")} />
        </Form.Item>

        <Form.Item name="tags" label={t("标签")}>
          <Select mode="tags" placeholder={t("输入后回车添加标签")} tokenSeparators={[',', '，']} />
        </Form.Item>

        <div className={workflowStyles.formGridCompact}>
          <Form.Item
            name="scheduled_at"
            label={t("时间")}
            rules={[{ required: true, message: t("请选择时间") }]}
          >
            <DatePicker showTime style={{ width: '100%' }} format="YYYY-MM-DD HH:mm" />
          </Form.Item>
          <Form.Item
            name="duration_minutes"
            label={t("时长")}
            rules={[{ required: true, message: t("请输入时长") }]}
          >
            <InputNumber min={1} precision={0} addonAfter={t("分钟")} style={{ width: '100%' }} />
          </Form.Item>
        </div>

        <div className={workflowStyles.formGridStatus}>
          <Form.Item name="remind_at" label={t("提醒时间")}>
            <DatePicker showTime style={{ width: '100%' }} format="YYYY-MM-DD HH:mm" />
          </Form.Item>
          <Form.Item name="status" label={t("状态")}>
            <Select
              options={[
                { value: 'todo', label: t("待处理") },
                { value: 'done', label: t("已完成") },
                { value: 'cancelled', label: t("已取消") },
              ]}
            />
          </Form.Item>
        </div>

        <Form.Item name="location" label={t("地点")}>
          <Input placeholder={t("线上链接或线下地址")} />
        </Form.Item>

        <Form.Item name="notes" label={t("备注")}>
          <Input.TextArea rows={3} placeholder={t("准备事项、联系人等")} />
        </Form.Item>
      </Form>
    </section>
  );
}
