import { useEffect, useState } from 'react';
import { Button, Modal, Upload, message } from 'antd';
import { InboxOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd/es/upload/interface';

interface Props {
  open: boolean;
  uploading: boolean;
  onSubmit: (file: File) => void;
  onClose: () => void;
}

export default function ResumeUploadModal({ open, uploading, onSubmit, onClose }: Props) {
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    if (!open) setFile(null);
  }, [open]);

  const fileList: UploadFile[] = file
    ? [{ uid: file.name, name: file.name, status: 'done', size: file.size }]
    : [];

  return (
    <Modal
      title="上传简历"
      open={open}
      onCancel={onClose}
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button
          key="submit"
          type="primary"
          loading={uploading}
          disabled={!file}
          onClick={() => file && onSubmit(file)}
        >
          上传
        </Button>,
      ]}
    >
      <Upload.Dragger
        accept=".pdf"
        multiple={false}
        maxCount={1}
        fileList={fileList}
        beforeUpload={(next) => {
          if (next.size > 10 * 1024 * 1024) {
            message.error('文件过大，最大 10MB');
            return false;
          }
          setFile(next);
          return false;
        }}
        onRemove={() => { setFile(null); return true; }}
      >
        <p className="ant-upload-drag-icon"><InboxOutlined /></p>
        <p className="ant-upload-text">将 PDF 简历拖到这里</p>
        <p className="ant-upload-hint">仅支持 .pdf · 单文件最大 10MB</p>
      </Upload.Dragger>
    </Modal>
  );
}