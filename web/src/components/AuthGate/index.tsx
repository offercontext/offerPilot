import { LockOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Input, Skeleton, Space, Typography } from 'antd';
import { type ReactNode, useState } from 'react';
import { getAuthStatus } from '@/services/auth';
import { setStoredAuthToken } from '@/services/authToken';
import { shouldPromptForAuth } from './model';

interface Props {
  children: ReactNode;
}

export default function AuthGate({ children }: Props) {
  const [token, setToken] = useState('');
  const [invalid, setInvalid] = useState(false);
  const statusQuery = useQuery({
    queryKey: ['auth-status'],
    queryFn: getAuthStatus,
    retry: false,
  });

  if (statusQuery.isLoading) {
    return (
      <main style={shellStyle}>
        <section style={panelStyle}>
          <Skeleton active paragraph={{ rows: 3 }} />
        </section>
      </main>
    );
  }

  if (!shouldPromptForAuth(statusQuery.data)) {
    return children;
  }

  async function submitToken() {
    setStoredAuthToken(token);
    const result = await statusQuery.refetch();
    setInvalid(Boolean(result.data?.auth_enabled && !result.data.authenticated));
  }

  return (
    <main style={shellStyle}>
      <section style={panelStyle}>
        <Space align="start" size={12}>
          <span style={{ color: 'var(--op-primary)', fontSize: 22, lineHeight: 1 }}>
            <LockOutlined />
          </span>
          <div>
            <Typography.Title level={3} style={{ margin: 0, color: 'var(--op-ink)' }}>
              OfferPilot
            </Typography.Title>
            <Typography.Text style={{ color: 'var(--op-muted)' }}>
              Enter the local access token for this workspace.
            </Typography.Text>
          </div>
        </Space>
        {invalid ? <Alert type="error" showIcon message="Invalid token" /> : null}
        <Input.Password
          value={token}
          onChange={(event) => setToken(event.target.value)}
          onPressEnter={submitToken}
          placeholder="Access token"
          autoFocus
        />
        <Button type="primary" onClick={submitToken} loading={statusQuery.isFetching} block>
          Continue
        </Button>
      </section>
    </main>
  );
}

const shellStyle = {
  minHeight: '100vh',
  display: 'grid',
  placeItems: 'center',
  padding: 24,
  background: 'var(--op-bg)',
} as const;

const panelStyle = {
  width: 'min(420px, 100%)',
  display: 'grid',
  gap: 16,
  padding: 24,
  background: 'var(--op-surface)',
  border: '1px solid var(--op-border)',
  borderRadius: 8,
  boxShadow: 'var(--op-shadow-sm)',
} as const;
