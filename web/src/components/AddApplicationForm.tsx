import { useState } from 'react';
import { Modal, Input, Form, Select, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { createApplication } from '@/services/applications';
import { ONBOARDING_QUERY_KEY } from '@/services/onboarding';
import { STATUS_LABELS } from '@/types/application';
import type { ApplicationStatus } from '@/types/application';

interface AddApplicationFormProps {
  open: boolean;
  onClose: () => void;
}

const STATUS_OPTIONS = (Object.entries(STATUS_LABELS) as [ApplicationStatus, string][]).map(
  ([value, label]) => ({ value, label })
);

export default function AddApplicationForm({ open, onClose }: AddApplicationFormProps) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);
  const status = Form.useWatch('status', form) as ApplicationStatus | undefined;

  const handleOk = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      await createApplication({
        company_name: values.company_name,
        position_name: values.position_name,
        job_url: values.job_url ?? '',
        status: values.status ?? 'applied',
        notes: values.notes ?? '',
        closed_reason: values.closed_reason ?? '',
      });
      queryClient.invalidateQueries({ queryKey: ['applications'] });
      queryClient.invalidateQueries({ queryKey: ONBOARDING_QUERY_KEY });
      message.success('已添加投递');
      form.resetFields();
      onClose();
    } catch (err) {
      // validateFields rejects on validation error; only show error for non-validation failures
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error('添加失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="添加投递"
      open={open}
      onOk={handleOk}
      onCancel={() => {
        form.resetFields();
        onClose();
      }}
      confirmLoading={submitting}
      okText="添加"
      cancelText="取消"
    >
      <Form form={form} layout="vertical" initialValues={{ status: 'applied' }}>
        <Form.Item
          name="company_name"
          label="公司"
          rules={[{ required: true, message: '请输入公司名称' }]}
        >
          <Input placeholder="例如：字节跳动" />
        </Form.Item>
        <Form.Item
          name="position_name"
          label="岗位"
          rules={[{ required: true, message: '请输入岗位名称' }]}
        >
          <Input placeholder="例如：前端工程师" />
        </Form.Item>
        <Form.Item name="job_url" label="JD 链接">
          <Input placeholder="https://..." />
        </Form.Item>
        <Form.Item name="status" label="状态">
          <Select options={STATUS_OPTIONS} />
        </Form.Item>
        {status === 'closed' && (
          <Form.Item
            name="closed_reason"
            label="关闭原因"
            rules={[{ required: true, message: '请输入关闭原因' }]}
          >
            <Input.TextArea rows={2} placeholder="例如：岗位关闭、主动放弃、已接受其他 offer" />
          </Form.Item>
        )}
        <Form.Item name="notes" label="备注">
          <Input.TextArea rows={2} placeholder="内推人、岗位亮点等" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
