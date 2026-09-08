// Application status lifecycle. Mirrors the Go db.Application.Status values.
export type ApplicationStatus =
  | 'pending'
  | 'applied'
  | 'written_test'
  | 'interview'
  | 'offer'
  | 'closed';

// Application record — fields match the Go JSON tags (snake_case).
export interface Application {
  id: number;
  company_name: string;
  position_name: string;
  job_url: string;
  status: ApplicationStatus;
  source: string;
  notes: string;
  applied_at: string;
  first_pending_at?: string | null;
  first_applied_at?: string | null;
  first_written_test_at?: string | null;
  first_interview_at?: string | null;
  first_offer_at?: string | null;
  closed_reason?: string;
  closed_at?: string | null;
  deleted_at?: string | null;
  created_at: string;
  updated_at: string;
}

// Payload for creating/updating an application.
export interface ApplicationInput {
  company_name: string;
  position_name: string;
  job_url?: string;
  status?: ApplicationStatus;
  notes?: string;
  closed_reason?: string;
}

export interface DashboardSummary {
  total: number;
  board: Record<string, Application[]>;
}

// Column definitions for the kanban board, in lifecycle order.
export const STATUS_LABELS: Record<ApplicationStatus, string> = {
  pending: '待投递',
  applied: '已投递',
  written_test: '笔试',
  interview: '面试',
  offer: 'Offer',
  closed: '结束',
};

export const STATUS_COLORS: Record<ApplicationStatus, string> = {
  pending: '#64748b',
  applied: '#0284c7',
  written_test: '#ea580c',
  interview: '#059669',
  offer: '#16a34a',
  closed: '#475569',
};

export const KANBAN_COLUMNS: ApplicationStatus[] = [
  'pending',
  'applied',
  'written_test',
  'interview',
  'offer',
  'closed',
];
