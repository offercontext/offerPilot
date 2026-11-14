import { liveText } from '@/i18n/liveText';
import { t, useLocale } from '@/i18n';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Typography,
  Tag,
  Timeline,
  Button,
  Input,
  message,
  Empty,
  Spin,
  Popconfirm,
  Space,
  Modal,
  Dropdown,
  Alert,
} from 'antd';
import {
  ArrowLeftOutlined,
  CalendarOutlined,
  PlusOutlined,
  MoreOutlined,
  FileTextOutlined,
  HistoryOutlined,
  EditOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import JobDescriptionContent from './JobDescriptionContent';
import type { Application } from '@/types/application';
import type { Offer } from '@/types/offer';
import type { PilotActionRequest } from '@/types/chat';
import { listNotesByApp, createNote, deleteNote as removeNote, updateNote } from '@/services/notes';
import { listEvents } from '@/services/events';
import type { CreateNoteInput, InterviewNote } from '@/types/note';
import type { ScheduleEvent } from '@/types/event';
import { EVENT_TYPE_LABELS } from '@/types/event';
import { OFFER_STATUS_LABELS } from '@/types/offer';
import ScheduleEventForm from '@/components/ScheduleEventForm';
import ReviewFormDrawer from './ReviewFormDrawer';
import InterviewReviewProposalDrawer, {
  type InterviewReviewProposalAttemptState,
} from './InterviewReviewProposalDrawer';
import InterviewKnowledgeCaptureDrawer, {
  createInterviewKnowledgeCaptureDraft,
  type InterviewKnowledgeCaptureDraft,
} from './InterviewKnowledgeCaptureDrawer';
import InterviewPreparationProposalDrawer, {
  type InterviewPreparationDraft,
  type InterviewPreparationAttemptState,
  type InterviewPreparationKnowledgeOption,
} from './InterviewPreparationProposalDrawer';
import type { Resume } from '@/types/resume';
import MaterialKitDrawer from './MaterialKitDrawer';
import { materialKitOwnerStore } from '@/features/materialSurfaces/materialKitOwnerStore';
import OpportunityFitReviewDrawer, {
  type OpportunityFitOwnerProjection,
  type OpportunityFitOwnerStore,
} from './OpportunityFitReviewDrawer';
import ApplicationOutcomeDrawer from './ApplicationOutcomeDrawer';
import OfferNegotiationDrawer, { type OfferNegotiationDraft } from './OfferNegotiationDrawer';
import type { OfferNegotiationPilotBrief } from '@/features/offerNegotiation/pilotHandoff';
import { getApplicationMaterialKit } from '@/services/materialKits';
import { listOpportunityFitV2Reviews } from '@/services/opportunityFitReviews';
import {
  type OpportunityFitReview,
} from '@/types/opportunityFitReview';
import { createPilotAttachmentDragBinding } from './PilotAttachmentHandle';
import { consumeMaterialKitHandoff, materialKitHandoffStore } from '@/features/pilot/materialKitHandoff';
import {
  getCurrentApplicationJd,
  getApplicationJdVersion,
  listApplicationJdVersions,
  saveApplicationJdVersion,
} from '@/services/applicationJdVersions';
import type { ApplicationJdDraft } from '@/types/applicationJdVersion';
import NextStepSuggestions from './NextStepSuggestions';
import type {
  NextStepDestination,
  NextStepSuggestions as NextStepSuggestionsModel,
  ReadonlyDestination,
  SuggestionSessionState,
} from '@/lib/nextStepSuggestions';
import styles from './ApplicationDetail.module.css';
import { getApplicationWorkspaceStage } from './applicationWorkspaceModel';
import { normalizeInterviewIndexItem } from '@/features/interviewEvents/interviewIndexContract';
import { projectInterviewEventCard } from '@/features/interviewEvents/interviewEventCard';
import { createCoreTaskSurfaceController, requestCoreTaskClose, type ActiveCoreTask, type CoreTaskLaunchResult, type CoreTaskSurfaceController } from '@/features/coreTaskSurface/controller';
import { CoreTaskSurfaceHost } from '@/features/coreTaskSurface/CoreTaskSurfaceHost';
import type { TaskLaunchRequest } from '@/features/coreTaskSurface/contracts';
import { isReviewReadinessDraftPending, isReviewReadinessDraftUnsaved, type ReviewReadinessOwnerDraft } from '@/features/reviewReadiness/contracts';
import {
  resolveApplicationTasks,
  type ApplicationTaskEvent,
  type ApplicationTaskReview,
  type ApplicationTaskResolution,
  type FrozenApplicationTaskSnapshot,
  type TaskSource,
} from '@/features/applicationTasks/applicationTaskResolver';

const { Title, Paragraph, Text } = Typography;

type ApplicationDetailTab = 'overview' | 'preparation' | 'progress';

interface ScheduleFormNavigationSnapshot {
  activeTab: ApplicationDetailTab;
  scrollTop: number;
  content: HTMLElement | null;
  trigger: HTMLElement | null;
  triggerKind: 'stage' | 'more' | 'schedule' | null;
}

type ScheduleFormReturn =
  | { kind: 'restore'; snapshot: ScheduleFormNavigationSnapshot }
  | { kind: 'schedule' };

function getApplicationContent(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  return document.querySelector<HTMLElement>('.op-app-content');
}

function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function getScheduleTrigger(
  triggerKind: ScheduleFormNavigationSnapshot['triggerKind'],
): HTMLElement | null {
  if (typeof document === 'undefined' || triggerKind === null) return null;
  const testId = triggerKind === 'stage'
    ? 'application-stage-action'
    : triggerKind === 'more'
      ? 'application-more-actions'
      : 'application-schedule-create';
  return document.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
}

const DETAIL_TABS: Array<{ id: ApplicationDetailTab; label: string }> = [
  { id: 'overview', get label() { return t("概览"); } },
  { id: 'preparation', get label() { return t("准备"); } },
  { id: 'progress', get label() { return t("进展"); } },
];

interface ApplicationProgressItem {
  id: string;
  title: string;
  timestamp?: string | null;
  detail?: string;
  kind: 'application' | 'event' | 'note' | 'offer';
}

const EVENT_SUBTYPE_LABELS: Readonly<Record<string, string>> = {
  get assessment() { return t("测评"); },
  get technical() { return t("技术面试"); },
  get behavioral() { return t("行为面试"); },
  get phone() { return t("电话沟通"); },
  get onsite() { return t("现场面试"); },
  get hr() { return t("人事面试"); },
  get final() { return t("终面"); },
  get screening() { return t("初筛"); },
};

const EVENT_STATUS_LABELS: Readonly<Record<string, string>> = {
  get todo() { return t("待处理"); },
  get pending() { return t("待确认"); },
  get scheduled() { return t("已安排"); },
  get in_progress() { return t("进行中"); },
  get done() { return t("已完成"); },
  get completed() { return t("已完成"); },
  get cancelled() { return t("已取消"); },
  get deleted() { return t("已取消"); },
  get soft_deleted() { return t("已取消"); },
};

function eventSubtypeLabel(value: string): string {
  if (!value) return '';
  return EVENT_SUBTYPE_LABELS[value] ?? (/\p{Script=Han}/u.test(value) ? value : t("其他环节"));
}

function eventStatusLabel(value: string): string {
  return EVENT_STATUS_LABELS[value] ?? t("状态待确认");
}

function formatWorkspaceDate(value?: string | null, fallback = t("待记录")) {
  if (!value) return fallback;
  const date = dayjs(value);
  return date.isValid() ? date.format('YYYY-MM-DD HH:mm') : fallback;
}

function workspaceTimestamp(value?: string | null) {
  if (!value) return 0;
  const timestamp = dayjs(value).valueOf();
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function isObjectRow(value: unknown): boolean {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const TASK_COPY: Readonly<Record<string, { title: string; description: string; action: string }>> = Object.freeze({
  'application.opportunity_fit': { get title() { return t("岗位匹配与风险"); }, get description() { return t("确认是否值得继续，以及需要补充的事实。"); }, get action() { return t("开始判断"); } },
  'application.material_kit': { get title() { return t("关联材料"); }, get description() { return t("核对本次投递关联的简历与材料记录。"); }, get action() { return t("查看投递材料"); } },
  'application.interview_prepare': { get title() { return t("面试准备"); }, get description() { return t("围绕这场面试整理准备信息。"); }, get action() { return t("开始准备"); } },
  'application.interview_review': { get title() { return t("面试复盘"); }, get description() { return t("记录或查看这场面试的复盘。"); }, get action() { return t("打开复盘"); } },
  'application.general_review': { get title() { return t("投递复盘"); }, get description() { return t("查看不绑定具体事件的投递复盘。"); }, get action() { return t("打开复盘"); } },
  'application.offer_review': { get title() { return t("Offer 准备"); }, get description() { return t("查看归属本次投递的 Offer 与准备信息。"); }, get action() { return t("查看准备"); } },
  'application.record_outcome': { get title() { return t("投递结果"); }, get description() { return t("记录本次投递的事实与结果。"); }, get action() { return t("打开记录"); } },
});

function taskSource<T = never>(status: 'loading' | 'error' | 'ready' | 'absent', value?: T): TaskSource<T> {
  const immutableValue = value && typeof value === 'object' ? Object.freeze(value) : value;
  if (status === 'ready') return Object.freeze({ status: 'ready' as const, value: immutableValue as T });
  if (status === 'loading') return Object.freeze({ status: 'loading' as const });
  if (status === 'absent') return Object.freeze({ status: 'absent' as const });
  return Object.freeze({ status: 'error' as const });
}

function taskLaunchAccepted(result: CoreTaskLaunchResult): boolean {
  return result.kind === 'launched' || result.kind === 'focused_existing';
}

function emptyTaskSnapshot(): FrozenApplicationTaskSnapshot {
  return Object.freeze({
    application: taskSource('absent'),
    jd: taskSource('absent'),
    events: taskSource('absent'),
    offers: taskSource('absent'),
    materialKit: taskSource('absent'),
    reviews: taskSource('absent'),
    fit: taskSource('absent'),
    resume: taskSource('absent'),
    pending: taskSource('absent'),
    resultUnknown: taskSource('absent'),
  });
}

function projectApplicationEvent(
  event: ScheduleEvent,
  note: InterviewNote | undefined,
  now: number,
): ApplicationTaskEvent {
  // Missing ownership is intentionally represented as 0 so the resolver marks
  // the row unavailable instead of silently rebinding a legacy payload.
  const applicationId = event.application_id ?? 0;
  const schedule = event.scheduled_at;
  const scheduledAtState = typeof schedule === 'string' && schedule.length > 0 ? 'present' : 'absent';
  const normalized = normalizeInterviewIndexItem({
    application_id: applicationId,
    event_id: event.id,
    company_name: '',
    position_name: '',
    scheduled_at: schedule ?? '',
    scheduled_at_state: scheduledAtState,
    event_status: event.status,
    duration_minutes: event.duration_minutes,
    note_id: note?.id ?? null,
    note_source_status: null,
    has_review_proposal: false,
    review_summary: null,
    has_confirmed_knowledge: false,
    preparation_available: true,
  });
  const card = projectInterviewEventCard(normalized, now);
  return Object.freeze({
    applicationId,
    eventId: event.id,
    lifecycle: card.lifecycle,
    bucket: card.bucket,
    primaryAction: card.primaryAction,
    scheduledAtTimestamp: normalized.scheduleTimestamp,
    durationMinutes: normalized.duration_minutes,
    scheduledAtState,
  });
}

export interface ApplicationInterviewChoice {
  readonly eventId: number;
  readonly label: string;
  readonly scheduledAtTimestamp: number | null;
  readonly lifecycle: ApplicationTaskEvent['lifecycle'];
}

export interface ApplicationInterviewChoices {
  readonly preparation: readonly ApplicationInterviewChoice[];
  readonly review: readonly ApplicationInterviewChoice[];
}

/**
 * Project an application-only chooser from the same lifecycle contract used
 * by task resolution. Duplicate identities and malformed/foreign rows are
 * omitted instead of letting input order choose an owner.
 */
export function projectApplicationInterviewChoices(
  events: readonly ScheduleEvent[],
  applicationId: number,
  now: number,
): ApplicationInterviewChoices {
  const preparation: ApplicationInterviewChoice[] = [];
  const review: ApplicationInterviewChoice[] = [];
  if (!Number.isSafeInteger(applicationId) || applicationId <= 0 || !Number.isFinite(now)) {
    return Object.freeze({ preparation: Object.freeze(preparation), review: Object.freeze(review) });
  }
  try {
    if (!Array.isArray(events)) throw new TypeError('invalid events');
    const unique = new Map<number, ScheduleEvent | null>();
    for (let index = 0; index < events.length; index += 1) {
      if (!(index in events)) throw new TypeError('sparse events');
      const candidate = events[index];
      let eventId: unknown;
      try {
        eventId = candidate?.id;
      } catch {
        continue;
      }
      if (typeof eventId !== 'number' || !Number.isSafeInteger(eventId) || eventId <= 0) continue;
      unique.set(eventId, unique.has(eventId) ? null : candidate);
    }

    for (const [eventId, event] of unique) {
      if (!event) continue;
      try {
        if (event.application_id !== applicationId || event.event_type !== 'interview') continue;
        const projected = projectApplicationEvent(event, undefined, now);
        const subtype = typeof event.subtype === 'string' && event.subtype.trim()
          ? event.subtype.trim().slice(0, 80)
          : t("面试");
        const timeLabel = projected.scheduledAtTimestamp === null
          ? t("时间待确认")
          : formatWorkspaceDate(event.scheduled_at, t("时间待确认"));
        const choice = Object.freeze({
          eventId,
          label: `${subtype} · ${timeLabel}`,
          scheduledAtTimestamp: projected.scheduledAtTimestamp,
          lifecycle: projected.lifecycle,
        });
        const scheduledWithinWindow = projected.lifecycle === 'scheduled'
          && projected.bucket === 'upcoming'
          && projected.primaryAction === 'prepare'
          && projected.scheduledAtTimestamp !== null
          && projected.scheduledAtTimestamp > now
          && projected.scheduledAtTimestamp - now <= 24 * 60 * 60_000;
        const currentInProgress = projected.lifecycle === 'in_progress'
          && projected.bucket === 'upcoming'
          && projected.primaryAction === 'enter_preparation';
        if (scheduledWithinWindow || currentInProgress) preparation.push(choice);
        if (projected.lifecycle === 'completed'
          && projected.bucket === 'completed'
          && (projected.primaryAction === 'record_review' || projected.primaryAction === 'view_review')) {
          review.push(choice);
        }
      } catch {
        // A malformed row cannot become a chooser option.
      }
    }
  } catch {
    return Object.freeze({ preparation: Object.freeze([]), review: Object.freeze([]) });
  }
  const compare = (left: ApplicationInterviewChoice, right: ApplicationInterviewChoice): number => {
    if (left.scheduledAtTimestamp === null && right.scheduledAtTimestamp !== null) return 1;
    if (left.scheduledAtTimestamp !== null && right.scheduledAtTimestamp === null) return -1;
    if (left.scheduledAtTimestamp !== null && right.scheduledAtTimestamp !== null
      && left.scheduledAtTimestamp !== right.scheduledAtTimestamp) {
      return left.scheduledAtTimestamp < right.scheduledAtTimestamp ? -1 : 1;
    }
    return left.eventId - right.eventId;
  };
  preparation.sort(compare);
  review.sort(compare);
  return Object.freeze({
    preparation: Object.freeze(preparation),
    review: Object.freeze(review),
  });
}

interface ApplicationDetailProps {
  initialTab?: ApplicationDetailTab;
  application: Application | null;
  open: boolean;
  onClose: () => void;
  /** Injected by AppShell; all task entrypoints share this controller. */
  taskController?: CoreTaskSurfaceController;
  onLaunchTask?: (request: TaskLaunchRequest) => CoreTaskLaunchResult;
  /** Narrow owner-internal Fit → Material transition adapter. */
  onConfirmedFitToMaterial?: (request: TaskLaunchRequest) => CoreTaskLaunchResult;
  onTaskSurfaceGuardChange?: (guard: { pending: boolean; unsaved: boolean }) => void;
  onOpenOffers?: () => void;
  offers?: Offer[];
  offersLoading?: boolean;
  offersError?: boolean;
  onRetryOffers?: () => void;
  onMockInterview?: (app: Application) => void;
  onAskPilot?: (app: Application, action?: PilotActionRequest) => void;
  onOpenPilotOpportunityFit?: (app: Application) => void;
  pilotInterviewReviewApplicationId?: number | null;
  onPilotInterviewReviewFocusConsumed?: () => void;
  pilotInterviewPreparationApplicationId?: number | null;
  pilotInterviewPreparationEventId?: number | null;
  onPilotInterviewPreparationFocusConsumed?: () => void;
  onAttachToPilot?: (attachment: import('@/types/chat').PilotContextAttachment) => void;
  /** A global/foreign Assistant attempt that cannot be safely attributed here. */
  externalTaskBlocked?: boolean;
  /** Owner-local Fit state used to guard the canonical task and bounded projection. */
  onOpportunityFitOwnerStateChange?: (state: { pending: boolean; resultUnknown: boolean; unsaved: boolean }) => void;
  onOpportunityFitProjectionChange?: (projection: OpportunityFitOwnerProjection) => void;
  opportunityFitOwnerStore?: OpportunityFitOwnerStore;
  interviewReviewProposalAttempts?: Record<number, InterviewReviewProposalAttemptState>;
  onInterviewReviewProposalAttemptChange?: (
    noteID: number,
    state: InterviewReviewProposalAttemptState | null,
  ) => void;
  reviewReadinessDrafts?: Readonly<Record<string, ReviewReadinessOwnerDraft>>;
  onReviewReadinessDraftChange?: (
    key: string,
    draft: ReviewReadinessOwnerDraft | null,
    retireOwnerKey?: string,
  ) => boolean | void;
  onOpenReviewStory?: (noteId: number, focusId: string) => void;
  onOpenReadinessPractice?: (launch: import('@/features/reviewReadiness/contracts').ReadinessPracticeLaunch) => void;
  onInterviewNoteChanged?: (noteID: number) => void;
  interviewKnowledgeCaptureDrafts?: Record<number, InterviewKnowledgeCaptureDraft>;
  onInterviewKnowledgeCaptureDraftChange?: (noteID: number, draft: InterviewKnowledgeCaptureDraft | null) => void;
  onInterviewKnowledgeCaptureNoteChanged?: (noteID: number) => void;
  resumes?: Resume[];
  interviewPreparationAttempts?: Record<string, InterviewPreparationAttemptState>;
  onInterviewPreparationAttemptChange?: (key: string, state: InterviewPreparationAttemptState | null) => void;
  interviewPreparationDrafts?: Record<string, InterviewPreparationDraft>;
  onInterviewPreparationDraftChange?: (key: string, draft: InterviewPreparationDraft | null) => void;
  interviewPreparationKnowledgeOptions?: InterviewPreparationKnowledgeOption[];
  /** AppShell's generation-fenced result from the locked readiness surface. */
  interviewPreparationSelection?: {
    readonly generation: number;
    readonly applicationId: number;
    readonly eventId: number;
    readonly resumeId: number;
  } | null;
  offerNegotiationDrafts?: Record<number, OfferNegotiationDraft>;
  onOfferNegotiationDraftChange?: (offerId: number, draft: OfferNegotiationDraft | null) => void;
  onOpenOfferNegotiationPilot?: (offer: Offer, brief: OfferNegotiationPilotBrief) => boolean;
  offerNegotiationEntryPoint?: 'ui' | 'pilot';
  resumesLoading?: boolean;
  resumesError?: boolean;
  taskNow?: number;
  nextStepSuggestions?: NextStepSuggestionsModel;
  nextStepSessionState?: SuggestionSessionState | null;
  onSetDisposition?: (applicationId: number, suggestionId: string, state: SuggestionSessionState | null) => void;
  onNextStepNavigate?: (destination: NextStepDestination | ReadonlyDestination) => void;
  isNavigationAvailable?: (destination: NextStepDestination | ReadonlyDestination) => boolean;
  onNextStepReadonlyNavigate?: (destination: ReadonlyDestination) => void;
  isReadonlyNavigationAvailable?: (destination: ReadonlyDestination) => boolean;
  applicationJdDraft?: ApplicationJdDraft;
  onApplicationJdDraftChange?: (applicationId: number, patch: Partial<ApplicationJdDraft> | null) => void;
}

export default function ApplicationDetail({ initialTab = 'overview', application, open, onClose, taskController, onLaunchTask, onConfirmedFitToMaterial, onTaskSurfaceGuardChange, onOpenOffers, offers, offersLoading = false, offersError = false, onRetryOffers, onMockInterview: _onMockInterview, onAskPilot, onOpenPilotOpportunityFit: _onOpenPilotOpportunityFit, externalTaskBlocked = false, onOpportunityFitOwnerStateChange, onOpportunityFitProjectionChange, opportunityFitOwnerStore, pilotInterviewReviewApplicationId, onPilotInterviewReviewFocusConsumed, pilotInterviewPreparationApplicationId, pilotInterviewPreparationEventId, onPilotInterviewPreparationFocusConsumed, onAttachToPilot, interviewReviewProposalAttempts, onInterviewReviewProposalAttemptChange, reviewReadinessDrafts, onReviewReadinessDraftChange, onOpenReviewStory, onOpenReadinessPractice, onInterviewNoteChanged, interviewKnowledgeCaptureDrafts, onInterviewKnowledgeCaptureDraftChange, onInterviewKnowledgeCaptureNoteChanged, resumes, resumesLoading = false, resumesError = false, taskNow, interviewPreparationAttempts, onInterviewPreparationAttemptChange, interviewPreparationDrafts, onInterviewPreparationDraftChange, interviewPreparationKnowledgeOptions = [], interviewPreparationSelection, offerNegotiationDrafts = {}, onOfferNegotiationDraftChange, onOpenOfferNegotiationPilot, offerNegotiationEntryPoint = 'ui', nextStepSuggestions, nextStepSessionState = null, onSetDisposition, onNextStepNavigate, isNavigationAvailable, onNextStepReadonlyNavigate, isReadonlyNavigationAvailable, applicationJdDraft, onApplicationJdDraftChange }: ApplicationDetailProps) {
  const locale = useLocale();
  const queryClient = useQueryClient();
  const [eventFormOpen, setEventFormOpen] = useState(false);
  const [materialKitPrefill, setMaterialKitPrefill] = useState<{
    resumeID?: number;
    jdSnapshot?: string;
    jdVersionID?: number;
  }>({});
  const [editingNote, setEditingNote] = useState<InterviewNote | null>(null);
  const [knowledgeCaptureOpen, setKnowledgeCaptureOpen] = useState(false);
  const [pilotPreparationChoices, setPilotPreparationChoices] = useState<readonly ApplicationInterviewChoice[]>([]);
  const [pilotPreparationChooserOpen, setPilotPreparationChooserOpen] = useState(false);
  const [pilotReviewChoices, setPilotReviewChoices] = useState<readonly ApplicationInterviewChoice[]>([]);
  const [pilotReviewChooserOpen, setPilotReviewChooserOpen] = useState(false);
  const [jdEditorOpen, setJdEditorOpen] = useState(false);
  const [jdHistoryOpen, setJdHistoryOpen] = useState(false);
  const [selectedJdVersion, setSelectedJdVersion] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<ApplicationDetailTab>('overview');
  const [expandedJdId, setExpandedJdId] = useState<number | null>(null);
  const [expandedScheduleApplicationId, setExpandedScheduleApplicationId] = useState<number | null>(null);
  const scheduleFormHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const scheduleSectionRef = useRef<HTMLElement | null>(null);
  const scheduleNavigationRef = useRef<ScheduleFormNavigationSnapshot | null>(null);
  const scheduleReturnRef = useRef<ScheduleFormReturn | null>(null);
  const scheduleFormResultRef = useRef<'cancel' | 'success'>('cancel');
  const [opportunityFitOwnerState, setOpportunityFitOwnerState] = useState({
    pending: false,
    resultUnknown: false,
    unsaved: false,
  });
  const materialKitOwnerApplicationID = application?.id ?? 0;
  const materialKitOwnerSubscribe = useMemo(
    () => (listener: () => void) => materialKitOwnerStore.subscribe(materialKitOwnerApplicationID, listener),
    [materialKitOwnerApplicationID],
  );
  const materialKitOwnerGetSnapshot = useMemo(
    () => () => materialKitOwnerStore.getSnapshot(materialKitOwnerApplicationID),
    [materialKitOwnerApplicationID],
  );
  const materialKitOwnerSnapshot = useSyncExternalStore(
    materialKitOwnerSubscribe,
    materialKitOwnerGetSnapshot,
    materialKitOwnerGetSnapshot,
  );
  // The store snapshot is the only canonical material-owner state.  Deriving
  // this synchronously avoids a one-render mirror window in the host guard.
  const materialKitOwnerState = useMemo(() => ({
    applicationId: application?.id ?? null,
    pending: materialKitOwnerSnapshot.confirmation?.pending === true
      || materialKitOwnerSnapshot.proposal?.pending === true,
    resultUnknown: materialKitOwnerSnapshot.confirmation?.resultUnknown === true
      || materialKitOwnerSnapshot.proposal?.resultUnknown === true,
    sourceConflict: materialKitOwnerSnapshot.confirmation?.sourceConflict === true
      || materialKitOwnerSnapshot.proposal?.sourceConflict === true,
  }), [application?.id, materialKitOwnerSnapshot]);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const handledPilotReviewIntentRef = useRef<string | null>(null);
  const handledPilotPreparationIntentRef = useRef<string | null>(null);
  const standaloneTaskControllerRef = useRef<CoreTaskSurfaceController | null>(null);
  if (!taskController && !standaloneTaskControllerRef.current) {
    standaloneTaskControllerRef.current = createCoreTaskSurfaceController();
  }
  const effectiveTaskController = taskController ?? standaloneTaskControllerRef.current!;
  const localTaskSurfaceGuardRef = useRef({ pending: false, unsaved: false });
  const taskSurfaceState = useSyncExternalStore(
    effectiveTaskController.subscribe,
    effectiveTaskController.getState,
    effectiveTaskController.getState,
  );
  const activeTask = taskSurfaceState.active?.ref.applicationId === application?.id
    ? taskSurfaceState.active
    : null;
  useEffect(() => {
    setOpportunityFitOwnerState({ pending: false, resultUnknown: false, unsaved: false });
  }, [application?.id]);
  // AppShell supplies the shared clock snapshot; the standalone fallback is
  // deterministic and never lets the adapter read wall-clock state.
  const resolverNow = taskNow ?? 0;
  const offerRecords = Array.isArray(offers) ? offers : [];
  const resumeRecords = Array.isArray(resumes) ? resumes : [];

  const launchTask = (request: TaskLaunchRequest): CoreTaskLaunchResult => {
    const result = onLaunchTask?.(request) ?? effectiveTaskController.launch(request);
    if (result.kind === 'launched' || result.kind === 'focused_existing') setActiveTab('preparation');
    return result;
  };

  const isTaskGenerationCurrent = (generation: number): boolean => (
    effectiveTaskController.getState().active?.generation === generation
  );

  const closeTask = (generation: number): boolean => {
    const active = effectiveTaskController.getState().active;
    if (!active || active.generation !== generation) return false;
    return requestCoreTaskClose(effectiveTaskController, active, localTaskSurfaceGuardRef.current);
  };

  const applicationJdQuery = useQuery({
    queryKey: ['application-jd-current', application?.id],
    queryFn: () => getCurrentApplicationJd(application!.id),
    enabled: Boolean(application) && open,
  });
  const jdHistoryQuery = useQuery({
    queryKey: ['application-jd-history', application?.id],
    queryFn: () => listApplicationJdVersions(application!.id),
    enabled: Boolean(application) && open && jdHistoryOpen,
  });
  const jdDetailQuery = useQuery({
    queryKey: ['application-jd-detail', application?.id, selectedJdVersion],
    queryFn: () => getApplicationJdVersion(application!.id, selectedJdVersion!),
    enabled: Boolean(application) && open && selectedJdVersion !== null,
  });
  // These read-only adapters reuse the exact cache keys consumed by the
  // canonical drawers. They only project source state for task resolution;
  // the drawers remain the existing mutation/HITL owners.
  const materialKitQuery = useQuery({
    queryKey: ['application-material-kit', application?.id],
    queryFn: () => getApplicationMaterialKit(application!.id),
    enabled: Boolean(application) && open,
  });
  const opportunityFitHistoryQuery = useQuery({
    queryKey: ['opportunity-fit-v2-reviews', application?.id],
    queryFn: () => listOpportunityFitV2Reviews(application!.id),
    enabled: Boolean(application) && open,
    retry: false,
  });
  const jdSave = useMutation({
    mutationFn: (draft: ApplicationJdDraft) => saveApplicationJdVersion(application!.id, {
      jd_text: draft.jdText,
      source_url: draft.sourceUrl.trim() || null,
      expected_current_version_id: draft.expectedCurrentVersionId,
      idempotency_key: draft.idempotencyKey!,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['application-jd-current', application?.id] });
      queryClient.invalidateQueries({ queryKey: ['application-jd-history', application?.id] });
      onApplicationJdDraftChange?.(application!.id, null);
      setJdEditorOpen(false);
      message.success(liveText(() => t("岗位资料已保存")));
    },
    onError: (error: Error & { status?: number; code?: string }) => {
      if (error.code === 'application_jd_stale_current_version') {
        void applicationJdQuery.refetch().then(({ data }) => {
          onApplicationJdDraftChange?.(application!.id, {
            expectedCurrentVersionId: data?.current?.id ?? null,
            idempotencyKey: null,
            pendingOperation: null,
            resultUnknown: false,
          });
        });
        message.error(liveText(() => t("岗位资料已更新，已刷新当前版本，请确认后再保存")));
        return;
      }
      if (!error.status || error.status >= 500) {
        onApplicationJdDraftChange?.(application!.id, { resultUnknown: true, pendingOperation: 'save' });
        message.error(liveText(() => t("保存结果待确认，可使用原尝试重试")));
        return;
      }
      onApplicationJdDraftChange?.(application!.id, { idempotencyKey: null, pendingOperation: null, resultUnknown: false });
      message.error(liveText(() => t("岗位资料不能保存")));
    },
  });

  const startJdEditor = () => {
    if (applicationJdQuery.isLoading || applicationJdQuery.isError) return;
    const current = applicationJdQuery.data?.current;
    const draft = applicationJdDraft;
    onApplicationJdDraftChange?.(application!.id, {
      jdText: draft?.jdText ?? current?.jd_text ?? '',
      sourceUrl: draft?.sourceUrl ?? current?.source_url ?? '',
      expectedCurrentVersionId: draft?.expectedCurrentVersionId ?? current?.id ?? null,
      idempotencyKey: draft?.idempotencyKey ?? null,
      resultUnknown: draft?.resultUnknown ?? false,
      pendingOperation: draft?.pendingOperation ?? null,
    });
    setJdEditorOpen(true);
  };

  const submitJd = () => {
    if (!application || !applicationJdDraft) return;
    const key = applicationJdDraft.idempotencyKey ?? crypto.randomUUID().replace(/[^A-Za-z0-9_-]/g, '').slice(0, 32);
    const draft = { ...applicationJdDraft, idempotencyKey: key, pendingOperation: 'save' as const };
    onApplicationJdDraftChange?.(application.id, draft);
    jdSave.mutate(draft);
  };

  useEffect(() => {
    setMaterialKitPrefill({});
    setEventFormOpen(false);
    scheduleNavigationRef.current = null;
    scheduleReturnRef.current = null;
    scheduleFormResultRef.current = 'cancel';
    setActiveTab(initialTab);
    setPilotPreparationChooserOpen(false);
    setPilotPreparationChoices([]);
    setPilotReviewChooserOpen(false);
    setPilotReviewChoices([]);
    handledPilotReviewIntentRef.current = null;
    handledPilotPreparationIntentRef.current = null;
  }, [application?.id, open, initialTab]);

  useEffect(() => {
    const active = effectiveTaskController.getState().active;
    if (active && application?.id && active.ref.applicationId !== application.id) {
      // A remounted detail must never display another application's owner.
      if (requestCoreTaskClose(effectiveTaskController, active, localTaskSurfaceGuardRef.current)) {
        effectiveTaskController.markClosed(active.generation);
      }
    }
    return () => {
      const current = effectiveTaskController.getState().active;
      if (current && current.ref.applicationId === application?.id) {
        if (requestCoreTaskClose(effectiveTaskController, current, localTaskSurfaceGuardRef.current)) {
          effectiveTaskController.markClosed(current.generation);
        }
      }
    };
  }, [application?.id, effectiveTaskController, open, taskController]);

  useEffect(() => {
    if (!application || !open) return;
    const handoff = consumeMaterialKitHandoff(application.id);
    if (!handoff) return;
    const suggestedResumeId = handoff.hints?.suggestedResumeId;
    const alreadyActive = activeTask?.ref.taskId === 'application.material_kit'
      && activeTask.ref.applicationId === application.id;
    if (!alreadyActive) {
      const request: TaskLaunchRequest = {
        ref: { taskId: 'application.material_kit', applicationId: application.id },
        source: 'deep_link',
        hints: suggestedResumeId ? { suggestedResumeId } : undefined,
      };
      const confirmedFitHandoff = activeTask?.ref.taskId === 'application.opportunity_fit'
        && activeTask.ref.applicationId === application.id
        && handoff.source === 'pilot';
      const result = confirmedFitHandoff
        ? (onConfirmedFitToMaterial?.(request) ?? launchTask(request))
        : launchTask(request);
      if (result.kind !== 'launched' && result.kind !== 'focused_existing') {
        // Preserve a denied handoff for the eventual guarded retry. This is a
        // local navigation token, not a domain write.
        materialKitHandoffStore.write(handoff);
        return;
      }
    }
    setMaterialKitPrefill((current) => Object.keys(current).length > 0 ? current : {
      resumeID: suggestedResumeId,
    });
  }, [activeTask, application?.id, onConfirmedFitToMaterial, open]);

  const notesQuery = useQuery({
    queryKey: ['notes', application?.id],
    queryFn: () => listNotesByApp(application!.id),
    enabled: !!application,
  });

  const eventsQuery = useQuery({
    queryKey: ['events', application?.id],
    queryFn: () => listEvents({ application_id: application!.id }),
    enabled: !!application && open,
  });

  const eventRowsAreObjects = Array.isArray(eventsQuery.data) && eventsQuery.data.every(isObjectRow);
  const noteRowsAreObjects = Array.isArray(notesQuery.data) && notesQuery.data.every(isObjectRow);
  const allEvents: ScheduleEvent[] = eventRowsAreObjects ? (eventsQuery.data as ScheduleEvent[]) : [];
  const noteRecords: InterviewNote[] = noteRowsAreObjects ? (notesQuery.data as InterviewNote[]) : [];
  const canonicalInterviewChoices = useMemo(
    () => projectApplicationInterviewChoices(allEvents, application?.id ?? 0, resolverNow),
    [locale, allEvents, application?.id, resolverNow],
  );
  const interviewStageDataReady = application?.status !== 'interview'
    || (
      !eventsQuery.isLoading
      && !eventsQuery.isError
      && eventRowsAreObjects
      && !notesQuery.isLoading
      && !notesQuery.isError
      && noteRowsAreObjects
    );
  const interviewReviewDataReady = !eventsQuery.isLoading
    && !eventsQuery.isError
    && eventRowsAreObjects
    && !notesQuery.isLoading
    && !notesQuery.isError
    && noteRowsAreObjects;

  useEffect(() => {
    if (pilotInterviewReviewApplicationId == null) {
      handledPilotReviewIntentRef.current = null;
      return;
    }
    if (!application || !open || pilotInterviewReviewApplicationId !== application.id || !interviewReviewDataReady) return;
    const intentKey = `${application.id}:review`;
    if (handledPilotReviewIntentRef.current === intentKey) return;
    handledPilotReviewIntentRef.current = intentKey;
    // Pilot's application-only intent has no trusted Event identity. Always
    // show an explicit chooser, including the singleton case; only a user
    // click may construct the exact event review ref.
    setPilotReviewChoices(canonicalInterviewChoices.review);
    setPilotReviewChooserOpen(true);
    onPilotInterviewReviewFocusConsumed?.();
  }, [application, canonicalInterviewChoices.review, interviewReviewDataReady, open, onPilotInterviewReviewFocusConsumed, pilotInterviewReviewApplicationId]);

  useEffect(() => {
    if (pilotInterviewPreparationApplicationId == null) {
      handledPilotPreparationIntentRef.current = null;
      return;
    }
    if (!application || !open || pilotInterviewPreparationApplicationId !== application.id || eventsQuery.isLoading || eventsQuery.isError || !eventRowsAreObjects) return;
    const intentKey = `${application.id}:${pilotInterviewPreparationEventId ?? 'choose'}`;
    if (handledPilotPreparationIntentRef.current === intentKey) return;
    handledPilotPreparationIntentRef.current = intentKey;
    if (pilotInterviewPreparationEventId == null) {
      // An application-only intent enters an explicit chooser even when there
      // happens to be one event; event identity is never inferred here.
      setPilotPreparationChoices(canonicalInterviewChoices.preparation);
      setPilotPreparationChooserOpen(true);
    } else if (canonicalInterviewChoices.preparation.some(
      (choice) => choice.eventId === pilotInterviewPreparationEventId,
    )) {
      const result = launchTask({
        ref: {
          taskId: 'application.interview_prepare',
          applicationId: application.id,
          eventId: pilotInterviewPreparationEventId,
        },
        source: 'pilot',
        focus: 'current',
      });
      if (result.kind !== 'launched' && result.kind !== 'focused_existing') {
        handledPilotPreparationIntentRef.current = null;
        return;
      }
      setPilotPreparationChooserOpen(false);
    } else {
      setPilotPreparationChoices([]);
      setPilotPreparationChooserOpen(true);
    }
    onPilotInterviewPreparationFocusConsumed?.();
  }, [application, canonicalInterviewChoices.preparation, eventRowsAreObjects, eventsQuery.data, eventsQuery.isError, eventsQuery.isLoading, open, pilotInterviewPreparationEventId, onPilotInterviewPreparationFocusConsumed, pilotInterviewPreparationApplicationId]);

  const invalidateNotes = () => {
    if (application) queryClient.invalidateQueries({ queryKey: ['notes', application.id] });
    queryClient.invalidateQueries({ queryKey: ['notes', 'all'] });
  };

  const removeNoteMut = useMutation({
    mutationFn: (id: number) => removeNote(id),
    onSuccess: () => {
      message.success(liveText(() => t("已删除")));
      invalidateNotes();
    },
    onError: () => message.error(liveText(() => t("删除失败"))),
  });

  const updateNoteMut = useMutation({
    mutationFn: ({ id, input }: { id: number; input: CreateNoteInput }) => updateNote(id, input),
    onSuccess: (_data, variables) => {
      onInterviewNoteChanged?.(variables.id);
      onInterviewKnowledgeCaptureNoteChanged?.(variables.id);
      message.success(liveText(() => t("已更新面试复盘")));
      setEditingNote(null);
      invalidateNotes();
    },
    onError: () => message.error(liveText(() => t("更新失败"))),
  });

  const createEventNoteMut = useMutation({
    mutationFn: (input: CreateNoteInput) => createNote(application!.id, input),
    onSuccess: () => {
      message.success(liveText(() => t("已保存面试复盘")));
      invalidateNotes();
    },
    onError: () => message.error(liveText(() => t("保存复盘失败"))),
  });

  const closeDetail = () => {
    setEventFormOpen(false);
    scheduleNavigationRef.current = null;
    scheduleReturnRef.current = null;
    scheduleFormResultRef.current = 'cancel';
    setMaterialKitPrefill({});
    setEditingNote(null);
    setKnowledgeCaptureOpen(false);
    setPilotPreparationChoices([]);
    setPilotPreparationChooserOpen(false);
    setPilotReviewChoices([]);
    setPilotReviewChooserOpen(false);
    const active = effectiveTaskController.getState().active;
    if (active && active.ref.applicationId === application?.id
      && requestCoreTaskClose(effectiveTaskController, active, localTaskSurfaceGuardRef.current)) {
      effectiveTaskController.markClosed(active.generation);
    }
    onClose();
  };

  const openScheduleForm = (
    trigger: HTMLElement | null = null,
    triggerKind: ScheduleFormNavigationSnapshot['triggerKind'] = null,
  ) => {
    if (eventFormOpen) return;
    const content = trigger?.closest<HTMLElement>('.op-app-content') ?? getApplicationContent();
    scheduleNavigationRef.current = {
      activeTab,
      scrollTop: content?.scrollTop ?? 0,
      content,
      trigger,
      triggerKind,
    };
    scheduleFormResultRef.current = 'cancel';
    setEventFormOpen(true);
  };

  const markScheduleFormSuccess = () => {
    scheduleFormResultRef.current = 'success';
  };

  const closeScheduleForm = () => {
    const snapshot = scheduleNavigationRef.current;
    const result = scheduleFormResultRef.current;
    scheduleNavigationRef.current = null;
    scheduleFormResultRef.current = 'cancel';
    if (result === 'success') {
      scheduleReturnRef.current = { kind: 'schedule' };
      setActiveTab('preparation');
    } else if (snapshot) {
      scheduleReturnRef.current = { kind: 'restore', snapshot };
      setActiveTab(snapshot.activeTab);
    } else {
      scheduleReturnRef.current = null;
    }
    setEventFormOpen(false);
  };

  useEffect(() => {
    if (eventFormOpen) {
      const content = getApplicationContent();
      if (content) content.scrollTop = 0;
      scheduleFormHeadingRef.current?.focus({ preventScroll: true });
      return;
    }

    const pendingReturn = scheduleReturnRef.current;
    if (!pendingReturn) return;
    scheduleReturnRef.current = null;
    if (pendingReturn.kind === 'restore') {
      const content = pendingReturn.snapshot.content ?? getApplicationContent();
      if (content) content.scrollTop = pendingReturn.snapshot.scrollTop;
      const trigger = pendingReturn.snapshot.trigger?.isConnected
        ? pendingReturn.snapshot.trigger
        : getScheduleTrigger(pendingReturn.snapshot.triggerKind);
      trigger?.focus({ preventScroll: true });
      return;
    }

    scheduleSectionRef.current?.scrollIntoView({
      behavior: prefersReducedMotion() ? 'auto' : 'smooth',
      block: 'start',
    });
  }, [eventFormOpen]);

  const openKnowledgeCapture = (note: InterviewNote) => {
    const existing = interviewKnowledgeCaptureDrafts?.[note.id] ?? createInterviewKnowledgeCaptureDraft();
    onInterviewKnowledgeCaptureDraftChange?.(note.id, existing);
    setEditingNote(note);
    setKnowledgeCaptureOpen(true);
  };

  const activeTaskRuntimeState = useMemo(() => {
    if (!activeTask) return { pending: false, resultUnknown: false };
    const fitOwnerPending = activeTask.ref.taskId === 'application.opportunity_fit'
      && opportunityFitOwnerState.pending;
    const materialOwnerStateForApplication = materialKitOwnerState.applicationId === application?.id
      ? materialKitOwnerState
      : null;
    const reviewNote = activeTask.ref.taskId === 'application.interview_review' && activeTask.ref.eventId !== undefined
      ? noteRecords.find((note) => note.application_event_id === activeTask.ref.eventId)
      : undefined;
    const preparationAttempt = activeTask.ref.taskId === 'application.interview_prepare' && activeTask.ref.eventId !== undefined
      ? interviewPreparationAttempts?.[`${application?.id}:${activeTask.ref.eventId}`]
      : undefined;
    const reviewAttempt = reviewNote ? interviewReviewProposalAttempts?.[reviewNote.id] : undefined;
    const localPending = Boolean(preparationAttempt || reviewAttempt);
    const localResultUnknown = Boolean(
      (preparationAttempt?.result_unknown)
      || (reviewAttempt?.result_unknown)
      || (activeTask.ref.taskId === 'application.opportunity_fit' && opportunityFitOwnerState.resultUnknown)
      || (activeTask.ref.taskId === 'application.material_kit' && materialOwnerStateForApplication?.resultUnknown),
    );
    return {
      pending: fitOwnerPending || localPending || Boolean(
        activeTask.ref.taskId === 'application.material_kit' && materialOwnerStateForApplication?.pending,
      ),
      resultUnknown: localResultUnknown,
    };
  }, [activeTask, application?.id, interviewPreparationAttempts, interviewReviewProposalAttempts, materialKitOwnerState, noteRecords, notesQuery.data, opportunityFitOwnerState.pending, opportunityFitOwnerState.resultUnknown]);

  const taskSnapshot = useMemo<FrozenApplicationTaskSnapshot>(() => {
    if (!application) return emptyTaskSnapshot();
    const currentJd = applicationJdQuery.data?.current;
    const eventSource: FrozenApplicationTaskSnapshot['events'] = eventsQuery.isLoading
      ? taskSource('loading')
      : eventsQuery.isError
        ? taskSource('error')
        : eventsQuery.data === undefined
          ? taskSource('absent')
          : !eventRowsAreObjects
            ? taskSource('error')
            : taskSource('ready', Object.freeze(allEvents
              .filter((event) => event.event_type === 'interview')
              .map((event) => projectApplicationEvent(
                event,
                noteRecords.find((note) => note.application_event_id === event.id),
                resolverNow,
              ))));
    const reviewRows = Object.freeze(noteRecords.map((note) => Object.freeze({
      applicationId: application.id,
      // Preserve an omitted event identity as omitted. The resolver then
      // emits event_contract_invalid instead of rebinding it to application
      // scope; only an explicit null is a general review.
      eventId: note.application_event_id,
      reviewId: note.id,
    } as unknown as ApplicationTaskReview)));
    const reviewSource: FrozenApplicationTaskSnapshot['reviews'] = notesQuery.isLoading
      ? taskSource('loading')
      : notesQuery.isError
        ? taskSource('error')
        : notesQuery.data === undefined
          ? taskSource('absent')
          : !noteRowsAreObjects
            ? taskSource('error')
            : taskSource('ready', reviewRows);
    const jdSource: FrozenApplicationTaskSnapshot['jd'] = applicationJdQuery.isLoading
      ? taskSource('loading')
      : applicationJdQuery.isError
        ? taskSource('error')
        : applicationJdQuery.data === undefined
          ? taskSource('absent')
          : taskSource('ready', currentJd ? { id: currentJd.id, versionId: currentJd.id } : null);
    const offerSource: FrozenApplicationTaskSnapshot['offers'] = offersLoading
      ? taskSource('loading')
      : offersError
        ? taskSource('error')
          : offers === undefined
            ? taskSource('absent')
          : !Array.isArray(offers) || offerRecords.some((offer) => !isObjectRow(offer))
            ? taskSource('error')
            : taskSource('ready', Object.freeze(offerRecords.map((offer) => Object.freeze({
              id: offer.id,
              applicationId: offer.application_id ?? 0,
              status: offer.status,
              deadline: offer.deadline,
            }))));
    const resumeSource: FrozenApplicationTaskSnapshot['resume'] = resumesLoading
      ? taskSource('loading')
      : resumesError
        ? taskSource('error')
      : resumes === undefined
        ? taskSource('absent')
        : !Array.isArray(resumes) || resumeRecords.some((resume) => !isObjectRow(resume))
          ? taskSource('error')
        : taskSource('ready', resumeRecords.length === 1
          ? { id: resumeRecords[0].id, selected: true }
          : null);
    const materialSource: FrozenApplicationTaskSnapshot['materialKit'] = materialKitQuery.isLoading
      ? taskSource('loading')
      : materialKitQuery.isError
        ? taskSource('error')
        : materialKitQuery.data === undefined
          ? taskSource('absent')
          : taskSource('ready', materialKitQuery.data ? {
            applicationId: materialKitQuery.data.application_id,
            status: materialKitQuery.data.status,
            updatedAt: materialKitQuery.data.updated_at,
          } : null);
    const fitSource: FrozenApplicationTaskSnapshot['fit'] = opportunityFitHistoryQuery.isLoading
      ? taskSource('loading')
      : opportunityFitHistoryQuery.isError
        ? taskSource('error')
        : opportunityFitHistoryQuery.data === undefined
          ? taskSource('absent')
          : !Array.isArray(opportunityFitHistoryQuery.data)
            || !opportunityFitHistoryQuery.data.every(isObjectRow)
            ? taskSource('error')
          : taskSource('ready', (() => {
            const history = opportunityFitHistoryQuery.data;
            const latest = [...history].sort((left, right) => (
              (typeof right.id === 'number' ? right.id : 0)
              - (typeof left.id === 'number' ? left.id : 0)
            ))[0];
            return latest ? { reviewId: latest.review_id, status: latest.latest_stage?.stage_status } : null;
          })());
    const runtimePendingSource: FrozenApplicationTaskSnapshot['pending'] = externalTaskBlocked
      ? taskSource('error')
      : activeTaskRuntimeState.pending && !activeTaskRuntimeState.resultUnknown && activeTask
        ? taskSource('ready', { ref: activeTask.ref })
        : taskSource('ready', null);
    const runtimeUnknownSource: FrozenApplicationTaskSnapshot['resultUnknown'] = externalTaskBlocked
      ? taskSource('error')
      : activeTaskRuntimeState.resultUnknown && activeTask
        ? taskSource('ready', { ref: activeTask.ref })
        : taskSource('ready', null);
    return Object.freeze({
      application: taskSource('ready', { id: application.id, status: application.status }),
      jd: jdSource,
      events: eventSource,
      offers: offerSource,
      materialKit: materialSource,
      reviews: reviewSource,
      fit: fitSource,
      resume: resumeSource,
      pending: runtimePendingSource,
      resultUnknown: runtimeUnknownSource,
    });
  }, [activeTask, activeTaskRuntimeState.pending, activeTaskRuntimeState.resultUnknown, allEvents, application?.id, application?.status, applicationJdQuery.data, applicationJdQuery.data?.current, applicationJdQuery.isError, applicationJdQuery.isLoading, eventRowsAreObjects, eventsQuery.data, eventsQuery.isError, eventsQuery.isLoading, externalTaskBlocked, materialKitQuery.data, materialKitQuery.isError, materialKitQuery.isLoading, noteRecords, noteRowsAreObjects, notesQuery.data, notesQuery.isError, notesQuery.isLoading, offers, offersError, offersLoading, opportunityFitHistoryQuery.data, opportunityFitHistoryQuery.isError, opportunityFitHistoryQuery.isLoading, resolverNow, resumeRecords, resumes, resumesError, resumesLoading]);
  const applicationTaskResolution: ApplicationTaskResolution = useMemo(
    () => resolveApplicationTasks(taskSnapshot, resolverNow),
    [resolverNow, taskSnapshot],
  );
  const taskOwnerOpen = taskSurfaceState.phase !== 'closing';
  const launchMaterialKit = (
    prefill: { resumeID?: number; jdSnapshot?: string; jdVersionID?: number } = {},
    confirmedFitToMaterial = false,
    source: TaskLaunchRequest['source'] = 'application_task_card',
  ) => {
    if (!application) return;
    const request: TaskLaunchRequest = {
      ref: { taskId: 'application.material_kit', applicationId: application.id },
      source,
      hints: prefill.resumeID ? { suggestedResumeId: prefill.resumeID } : undefined,
    };
    const result = confirmedFitToMaterial
      ? (onConfirmedFitToMaterial?.(request) ?? launchTask(request))
      : launchTask(request);
    if (result.kind === 'launched') setMaterialKitPrefill(prefill);
    return result;
  };

  const renderTaskOwner = (active: ActiveCoreTask): ReactNode => {
    if (!application) return null;
    if (active.ref.applicationId !== application.id) return null;
    const isCurrent = () => isTaskGenerationCurrent(active.generation);
    const close = () => closeTask(active.generation);
    switch (active.ref.taskId) {
      case 'application.opportunity_fit':
        return (
          <OpportunityFitReviewDrawer
            application={application}
            open={taskOwnerOpen}
            currentJdText={applicationJdQuery.data?.current?.jd_text ?? ''}
            jdVersionId={applicationJdQuery.data?.current?.id ?? null}
            onOwnerStateChange={(state) => {
              if (!isCurrent()) return;
              setOpportunityFitOwnerState(state);
              onOpportunityFitOwnerStateChange?.(state);
            }}
            onOwnerProjectionChange={(projection) => {
              if (isCurrent()) onOpportunityFitProjectionChange?.(projection);
            }}
            ownerStore={opportunityFitOwnerStore}
            onApplicationMissing={() => {
              if (isCurrent()) onClose();
            }}
            onClose={close}
            onPrepareMaterials={(reviewOrResumeId: OpportunityFitReview | number, jdText: string, jdVersionId?: number) => {
              if (!isCurrent() || !jdVersionId) return;
              const resumeID = typeof reviewOrResumeId === 'number'
                ? reviewOrResumeId
                : reviewOrResumeId.source.resume.id;
              launchMaterialKit({ resumeID, jdSnapshot: jdText, jdVersionID: jdVersionId }, true);
            }}
          />
        );
      case 'application.material_kit': {
        const ownerState = materialKitOwnerState.applicationId === application.id
          ? materialKitOwnerState
          : { pending: false, resultUnknown: false, sourceConflict: false };
        return (
          <MaterialKitDrawer
            application={application}
            open={taskOwnerOpen}
            onClose={() => {
              if (!closeTask(active.generation)) return;
              setMaterialKitPrefill({});
            }}
            initialResumeID={materialKitPrefill.resumeID}
            initialJdSnapshot={materialKitPrefill.jdSnapshot}
            initialJdVersionID={materialKitPrefill.jdSnapshot && !materialKitPrefill.jdVersionID
              ? undefined
              : materialKitPrefill.jdVersionID ?? applicationJdQuery.data?.current?.id}
            pendingState={ownerState.pending ? 'pending' : 'none'}
            resultUnknown={ownerState.resultUnknown}
            sourceConflict={ownerState.sourceConflict}
            onOwnerStateChange={(state) => {
              if (!isCurrent()) return;
              materialKitOwnerStore.updateTransientState(application.id, state);
            }}
          />
        );
      }
      case 'application.interview_prepare': {
        const eventId = active.ref.eventId;
        if (eventId === undefined) return <div role="alert">{t("请先选择要准备的面试。")}</div>;
        const preparationTask = applicationTaskResolution.tasks.find(
          (task) => task.taskId === 'application.interview_prepare' && task.ref.eventId === eventId,
        );
        if (!preparationTask?.executable) {
          if (resumesLoading || eventsQuery.isLoading || eventsQuery.isFetching || notesQuery.isLoading || notesQuery.isFetching) {
            return <div role="status">{t("正在核对面试资料，请稍候。")}</div>;
          }
          return <div role="status">{t("该面试当前不可准备，请先确认日程状态。")}</div>;
        }
        const lockedSelection = interviewPreparationSelection
          && interviewPreparationSelection.generation === active.generation
          && interviewPreparationSelection.applicationId === application.id
          && interviewPreparationSelection.eventId === eventId
          && Number.isSafeInteger(interviewPreparationSelection.resumeId)
          && interviewPreparationSelection.resumeId > 0
          && resumeRecords.some((resume) => {
            try {
              const candidate = resume as Resume & { deleted?: boolean; hidden?: boolean; visible?: boolean };
              return candidate.id === interviewPreparationSelection.resumeId
                && candidate.deleted_at == null
                && candidate.deleted !== true
                && candidate.hidden !== true
                && candidate.visible !== false;
            } catch {
              return false;
            }
          })
          ? interviewPreparationSelection
          : null;
        if (taskController && !lockedSelection) {
          if (resumesLoading) {
            return <div role="status">{t("正在核对面试资料，请稍候。")}</div>;
          }
          return <div role="status">{t("请先在面试准备中心选择一份可用简历。")}</div>;
        }
        const preparationKey = `${application.id}:${eventId}`;
        return (
          <InterviewPreparationProposalDrawer
            key={`${application.id}:${eventId}:${active.generation}:${lockedSelection?.resumeId ?? 0}`}
            open={taskOwnerOpen}
            context={{
              applicationId: application.id,
              eventId,
              resumeId: lockedSelection?.resumeId ?? 0,
              jdText: applicationJdQuery.data?.current?.jd_text ?? '',
              jdVersionId: applicationJdQuery.data?.current?.id ?? null,
              knowledgeSelections: [],
              userAssertions: [],
            }}
            resumeOptions={resumeRecords}
            knowledgeOptions={interviewPreparationKnowledgeOptions}
            ownerGeneration={active.generation}
            attemptState={interviewPreparationAttempts?.[preparationKey]}
            draft={interviewPreparationDrafts?.[preparationKey]}
              onAttemptStateChange={(state) => {
                if (isCurrent()) onInterviewPreparationAttemptChange?.(preparationKey, state);
              }}
              onOpenPractice={onOpenReadinessPractice}
              onDraftChange={(draft) => {
                if (isCurrent()) onInterviewPreparationDraftChange?.(preparationKey, draft);
              }}
              onClose={close}
          />
        );
      }
      case 'application.interview_review':
      case 'application.general_review': {
        const eventId = active.ref.eventId;
        if (eventId !== undefined) {
          const reviewTask = applicationTaskResolution.tasks.find(
            (task) => task.taskId === 'application.interview_review' && task.ref.eventId === eventId,
          );
          if (!reviewTask?.executable) {
            if (eventsQuery.isLoading || eventsQuery.isFetching || notesQuery.isLoading || notesQuery.isFetching) {
              return <div role="status">{t("正在核对面试资料，请稍候。")}</div>;
            }
            return <div role="status">{t("该面试当前不可复盘，请先确认日程状态。")}</div>;
          }
        }
        const note = eventId === undefined
          ? noteRecords.find((item) => item.application_event_id === null)
          : noteRecords.find((item) => item.application_event_id === eventId);
        const compatibleEditingNote = editingNote && (
          eventId === undefined
            ? editingNote.application_event_id === null
            : editingNote.application_event_id === eventId
        ) ? editingNote : undefined;
        const ownerNote = note ?? compatibleEditingNote;
        if (eventId !== undefined && note) {
          return (
            <InterviewReviewProposalDrawer
              open={taskOwnerOpen}
              note={note}
              applicationId={application.id}
              eventID={eventId}
              attemptState={interviewReviewProposalAttempts?.[note.id]}
              ownerGeneration={active.generation}
              recoveryOwnerGeneration={active.recoveryGeneration}
              readinessDrafts={reviewReadinessDrafts}
              onReadinessDraftChange={onReviewReadinessDraftChange}
              onAttemptStateChange={(state) => {
                if (isCurrent()) onInterviewReviewProposalAttemptChange?.(note.id, state);
              }}
              onOpenStory={onOpenReviewStory}
              onClose={() => {
                if (!isTaskGenerationCurrent(active.generation)) return;
                setEditingNote(null);
                close();
              }}
            />
          );
        }
        return (
          <ReviewFormDrawer
            open={taskOwnerOpen}
            applications={[application]}
            initialApplication={application}
            note={ownerNote}
            initialEventID={eventId ?? null}
            saving={updateNoteMut.isPending || createEventNoteMut.isPending}
            onSubmit={(input) => {
              if (!isCurrent()) return;
              const currentNote = ownerNote;
              if (currentNote) updateNoteMut.mutate({ id: currentNote.id, input });
              else createEventNoteMut.mutate(input);
            }}
            onClose={() => {
              if (!isTaskGenerationCurrent(active.generation)) return;
              setEditingNote(null);
              close();
            }}
          />
        );
      }
      case 'application.offer_review': {
        if (offersLoading) return <div role="status">{t("Offer 信息正在读取。")}</div>;
        if (offersError) return <div role="alert">{t("Offer 信息暂时无法读取。")}</div>;
        if (offers === undefined) return <div role="status">{t("Offer 信息尚未加载。")}</div>;
        const linkedOffers = offerRecords.filter((offer) => offer.application_id === application.id);
        const suggestedOfferId = active.request.hints?.suggestedOfferId;
        const offer = linkedOffers.find((item) => item.id === suggestedOfferId)
          ?? (linkedOffers.length === 1 ? linkedOffers[0] : undefined);
        if (!offer) return <div role="status">{t("暂无可聚焦的 Offer，请先确认本次投递的 Offer。")}</div>;
        return (
          <OfferNegotiationDrawer
            open={taskOwnerOpen}
            offer={offer}
            entrypoint={offerNegotiationEntryPoint}
            draft={offerNegotiationDrafts[offer.id]}
            onDraftChange={(draft) => {
              if (isCurrent()) onOfferNegotiationDraftChange?.(offer.id, draft);
            }}
            onOpenPilotChat={onOpenOfferNegotiationPilot ? (currentOffer, brief) => {
              if (!isCurrent()) return;
              if (!onOpenOfferNegotiationPilot(currentOffer, brief)) return;
              close();
            } : undefined}
            onClose={close}
          />
        );
      }
      case 'application.record_outcome':
        return (
          <ApplicationOutcomeDrawer
            application={application}
            open={taskOwnerOpen}
            onClose={close}
            resumes={resumeRecords}
            currentJd={applicationJdQuery.data?.current ?? null}
            events={allEvents}
            onAskPilot={isCurrent() ? onAskPilot : undefined}
          />
        );
      default:
        return <div role="alert">{t("当前任务暂不可用，请稍后重试。")}</div>;
    }
  };

  useEffect(() => {
    const active = activeTask;
    const reviewNote = active?.ref.taskId === 'application.interview_review' && active.ref.eventId !== undefined
      ? noteRecords.find((note) => note.application_event_id === active.ref.eventId)
      : undefined;
    const pending = active && application
      ? Boolean(
      (active.ref.taskId === 'application.opportunity_fit'
        && (opportunityFitOwnerState.pending || opportunityFitOwnerState.resultUnknown))
      || (active.ref.taskId === 'application.material_kit'
        && materialKitOwnerState.applicationId === application.id
        && (materialKitOwnerState.pending || materialKitOwnerState.resultUnknown || materialKitOwnerState.sourceConflict))
      || (active.ref.taskId === 'application.interview_prepare' && active.ref.eventId !== undefined
        && interviewPreparationAttempts?.[`${application.id}:${active.ref.eventId}`])
      || (active.ref.taskId === 'application.interview_review' && active.ref.eventId !== undefined
        && reviewNote && (
          interviewReviewProposalAttempts?.[reviewNote.id]
          || Object.values(reviewReadinessDrafts ?? {}).some((draft) => (
            draft.ownerGeneration === active.generation
            && draft.noteId === reviewNote.id
            && isReviewReadinessDraftPending(draft)
          ))
        ))
      )
      : false;
    const unsaved = active
      ? Boolean(
      active.ref.taskId === 'application.opportunity_fit' && opportunityFitOwnerState.unsaved
      || active.ref.taskId === 'application.material_kit' && (
        Boolean(materialKitPrefill.jdSnapshot || materialKitPrefill.resumeID)
        || (
          materialKitOwnerSnapshot.generation > 0
          && Boolean(materialKitOwnerSnapshot.draft?.hasLocalState)
          && Boolean(materialKitOwnerSnapshot.draft?.draftDirty || materialKitOwnerSnapshot.draft?.resumeID || materialKitOwnerSnapshot.draft?.jdSnapshot)
        )
      )
      || active.ref.taskId === 'application.interview_prepare'
        && active.ref.eventId !== undefined
        && interviewPreparationDrafts?.[`${application?.id}:${active.ref.eventId}`]
      || active.ref.taskId === 'application.interview_review'
        && reviewNote
        && Object.values(reviewReadinessDrafts ?? {}).some((draft) => (
          draft.ownerGeneration === active.generation
          && draft.noteId === reviewNote.id
          && isReviewReadinessDraftUnsaved(draft)
        ))
      || active.ref.taskId === 'application.offer_review'
        && offerRecords.some((offer) => offer.application_id === application?.id && offerNegotiationDrafts?.[offer.id])
      )
      : false;
    localTaskSurfaceGuardRef.current = { pending, unsaved };
    onTaskSurfaceGuardChange?.({ pending, unsaved });
  }, [activeTask, application?.id, interviewPreparationAttempts, interviewPreparationDrafts, interviewReviewProposalAttempts, materialKitOwnerSnapshot, materialKitOwnerState, materialKitPrefill, noteRecords, notesQuery.data, offerNegotiationDrafts, offers, onTaskSurfaceGuardChange, opportunityFitOwnerState.pending, opportunityFitOwnerState.resultUnknown, opportunityFitOwnerState.unsaved, reviewReadinessDrafts]);

  if (!application || !open) return null;

  if (eventFormOpen) {
    return (
      <div className={styles.scheduleFormWorkspace} data-testid="application-schedule-form-surface">
        <ScheduleEventForm
          open
          applications={[application]}
          initialApplication={application}
          headingRef={scheduleFormHeadingRef}
          onSuccess={markScheduleFormSuccess}
          onClose={closeScheduleForm}
        />
      </div>
    );
  }

  const applicationDragBinding = onAttachToPilot
    ? createPilotAttachmentDragBinding({
        kind: 'application',
        id: String(application.id),
        label: `${application.company_name} · ${application.position_name}`,
      })
    : undefined;

  const headerPrimaryTask = applicationTaskResolution.primaryTask;
  const upcomingPrepareTask = applicationTaskResolution.tasks.find(
    (task) => task.taskId === 'application.interview_prepare' && task.ref.eventId !== undefined && task.executable,
  );
  const upcomingEvent = upcomingPrepareTask?.ref.eventId === undefined
    ? undefined
    : allEvents.find((event) => event.id === upcomingPrepareTask.ref.eventId);
  const primaryInterviewLifecycle = headerPrimaryTask?.taskId === 'application.interview_prepare'
    ? 'scheduled'
    : headerPrimaryTask?.taskId === 'application.interview_review'
      ? 'completed'
      : 'unknown';
  const stage = getApplicationWorkspaceStage(application.status, {
    lifecycle: application.status === 'interview' ? primaryInterviewLifecycle : undefined,
    hasInterviewReview: headerPrimaryTask?.reason === 'interview_review_available',
  });
  const stageDataBlocked = application.status === 'interview' && !interviewStageDataReady;
  const stageDataHasError = eventsQuery.isError || notesQuery.isError;
  const stageLabel = stageDataBlocked
    ? stageDataHasError ? t("面试进展暂不可读") : t("面试进展读取中")
    : stage.label;
  const stagePrimaryActionLabel = stageDataBlocked
    ? stageDataHasError ? t("重试日程和复盘") : t("等待面试进展加载")
    : application.status === 'interview'
      ? headerPrimaryTask?.taskId === 'application.interview_prepare'
        ? t("准备本轮面试")
        : headerPrimaryTask?.taskId === 'application.interview_review'
          ? headerPrimaryTask.reason === 'interview_review_available' ? t("查看本轮复盘") : t("完成面试复盘")
          : headerPrimaryTask
            ? TASK_COPY[headerPrimaryTask.taskId]?.action ?? t("打开当前任务")
            : t("暂无可用操作")
    : stage.primaryActionLabel;

  const retryStageData = () => {
    if (eventsQuery.isError) void eventsQuery.refetch();
    if (notesQuery.isError) void notesQuery.refetch();
  };

  const runStageAction = (trigger?: HTMLElement) => {
    if (stageDataBlocked) return;
    if (application.status === 'interview') {
      if (headerPrimaryTask) launchResolvedTask(headerPrimaryTask, 'application_header');
      return;
    }
    switch (stage.action) {
      case 'materials':
        {
          const currentJd = applicationJdQuery.data?.current;
          launchMaterialKit(currentJd ? { jdSnapshot: currentJd.jd_text, jdVersionID: currentJd.id } : {});
        }
        break;
      case 'followup':
      case 'written-test':
        openScheduleForm(trigger, 'stage');
        break;
      case 'offer': {
        const result = launchTask({
          ref: { taskId: 'application.offer_review', applicationId: application.id },
          source: 'application_header',
          focus: 'current',
        });
        // Standalone embedded callers historically used this callback to
        // navigate to the Offer collection. The composed AppShell keeps the
        // application-level owner mounted and therefore does not navigate.
        if (!taskController && result.kind === 'launched') onOpenOffers?.();
        break;
      }
      case 'outcome':
        launchTask({
          ref: { taskId: 'application.record_outcome', applicationId: application.id },
          source: 'application_header',
          focus: 'current',
        });
        break;
    }
  };

  const moreActionItems = [
    ...(onAskPilot ? [{ key: 'haru', label: t("让 Pilot 帮我"), onClick: () => onAskPilot(application, { type: 'application_jd_save' }) }] : []),
    { key: 'jd', label: applicationJdQuery.data?.current ? t("编辑岗位资料") : t("添加岗位资料"), onClick: startJdEditor },
    { key: 'schedule', label: t("安排日程"), onClick: () => openScheduleForm(getScheduleTrigger('more'), 'more') },
  ];

  const linkedOffers = offerRecords.filter((offer) => offer.application_id === application.id);
  const currentJd = applicationJdQuery.data?.current;
  const jdSource = currentJd?.source_url?.trim();
  const longJd = Boolean(currentJd && (currentJd.jd_text.length > 1200 || currentJd.jd_text.split('\n').length > 18));
  const jdExpanded = Boolean(currentJd && expandedJdId === currentJd.id);
  const materialKit = materialKitQuery.data;
  const materialOwnerMismatch = Boolean(materialKit && materialKit.application_id !== application.id);
  const materialTask = applicationTaskResolution.tasks.find((task) => task.taskId === 'application.material_kit');
  const linkedResume = !materialOwnerMismatch && materialKit
    ? resumeRecords.find((resume) => resume.id === materialKit.resume_id && !resume.deleted_at)
    : undefined;
  const materialActionLabel = application.status === 'pending' ? t("继续准备") : t("查看投递材料");
  const materialEntryBlocked = externalTaskBlocked || materialKitQuery.isLoading || materialKitQuery.isError
    || materialOwnerMismatch || Boolean(materialTask && !materialTask.executable)
    || (!materialTask && applicationTaskResolution.tasks.some((task) => task.availability === 'waiting_confirmation' || task.availability === 'result_unknown'));
  const remainingPreparationTasks = applicationTaskResolution.tasks.filter((task) => task.taskId !== 'application.material_kit');
  const visibleScheduleEvents = expandedScheduleApplicationId === application.id ? allEvents : allEvents.slice(0, 3);
  const progressItems: ApplicationProgressItem[] = [
    {
      id: `application-created-${application.id}`,
      kind: 'application' as const,
      title: t("创建投递"),
      timestamp: application.created_at,
      detail: application.source ? t("来源：{source}", { source: application.source }) : undefined,
    },
    {
      id: `application-updated-${application.id}`,
      kind: 'application' as const,
      title: t("当前阶段：{stageLabel}", { stageLabel: stageLabel }),
      timestamp: application.updated_at,
      detail: application.notes || undefined,
    },
    ...allEvents.map((event) => ({
      id: `event-${event.id}`,
      kind: 'event' as const,
      title: `${EVENT_TYPE_LABELS[event.event_type]}${event.subtype ? ` · ${eventSubtypeLabel(event.subtype)}` : ''}`,
      timestamp: event.scheduled_at,
      detail: [
        eventStatusLabel(event.status),
        event.location,
        event.notes,
      ].filter(Boolean).join(' · ') || undefined,
    })),
    ...noteRecords.map((note) => ({
      id: `note-${note.id}`,
      kind: 'note' as const,
      title: t("面试复盘{value1}", { value1: note.round ? ` · ${note.round}` : '' }),
      timestamp: note.created_at || note.date,
      detail: note.self_reflection || note.questions || note.difficulty_points || undefined,
    })),
    ...linkedOffers.map((offer) => ({
      id: `offer-${offer.id}`,
      kind: 'offer' as const,
      title: `Offer · ${offer.company_name} · ${offer.position_name}`,
      timestamp: offer.updated_at,
      detail: [
        t("状态：{value1}", { value1: OFFER_STATUS_LABELS[offer.status] }),
        offer.deadline ? t("截止：{value}", { value: formatWorkspaceDate(offer.deadline, t("未设置")) }) : undefined,
      ].filter(Boolean).join(' · '),
    })),
  ].sort((left, right) => workspaceTimestamp(right.timestamp) - workspaceTimestamp(left.timestamp));

  const launchResolvedTask = (
    task: ApplicationTaskResolution['tasks'][number],
    source: TaskLaunchRequest['source'] = 'application_task_card',
  ) => {
    if (!task.executable || externalTaskBlocked) return;
    if (task.taskId === 'application.material_kit') {
      const currentJd = applicationJdQuery.data?.current;
      launchMaterialKit(currentJd ? {
        jdSnapshot: currentJd.jd_text,
        jdVersionID: currentJd.id,
      } : {}, false, source);
      return;
    }
    launchTask({
      ref: task.ref,
      source,
      focus: task.taskId === 'application.offer_review' ? 'current' : 'overview',
    });
  };

  const launchNoteReview = (note: InterviewNote) => {
    const eventId = note.application_event_id;
    const task = eventId === null
      ? applicationTaskResolution.tasks.find(
        (candidate) => candidate.taskId === 'application.general_review'
          && candidate.ref.applicationId === application.id,
      )
      : typeof eventId === 'number' && Number.isSafeInteger(eventId) && eventId > 0
        ? applicationTaskResolution.tasks.find(
          (candidate) => candidate.taskId === 'application.interview_review'
            && candidate.ref.eventId === eventId,
        )
        : undefined;
    if (!task?.executable || externalTaskBlocked) {
      message.warning(liveText(() => t("该复盘当前不可用，请先确认事件与复盘资料。")));
      return;
    }
    const result = launchTask({
      ref: task.ref,
      source: 'application_task_card',
      focus: 'current',
    });
    if (taskLaunchAccepted(result)) setEditingNote(note);
  };

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const direction = event.key === 'ArrowRight' || event.key === 'ArrowDown'
      ? 1
      : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
        ? -1
        : 0;
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      const nextIndex = event.key === 'Home' ? 0 : DETAIL_TABS.length - 1;
      const nextTab = DETAIL_TABS[nextIndex];
      setActiveTab(nextTab.id);
      tabRefs.current[nextIndex]?.focus();
      return;
    }
    if (direction === 0) return;
    event.preventDefault();
    const nextIndex = (index + direction + DETAIL_TABS.length) % DETAIL_TABS.length;
    const nextTab = DETAIL_TABS[nextIndex];
    setActiveTab(nextTab.id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <>
      <Modal
        open={pilotPreparationChooserOpen}
        title={t("选择要准备的面试")}
        footer={null}
        onCancel={() => {
          setPilotPreparationChooserOpen(false);
          setPilotPreparationChoices([]);
        }}
      >
        {pilotPreparationChoices.length > 0 ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            {pilotPreparationChoices.map((choice) => (
              <Button
                key={choice.eventId}
                block
                disabled={externalTaskBlocked || !applicationTaskResolution.tasks.some(
                  (task) => task.taskId === 'application.interview_prepare'
                    && task.ref.eventId === choice.eventId
                    && task.executable,
                )}
                onClick={() => {
                  const task = applicationTaskResolution.tasks.find(
                    (candidate) => candidate.taskId === 'application.interview_prepare'
                      && candidate.ref.eventId === choice.eventId,
                  );
                  if (!task?.executable) return;
                  const result = launchTask({
                    ref: task.ref,
                    source: 'interview_event_card',
                    focus: 'current',
                  });
                  if (result.kind === 'launched' || result.kind === 'focused_existing') {
                    setPilotPreparationChooserOpen(false);
                    setPilotPreparationChoices([]);
                  }
                }}
              >
                {choice.label}
              </Button>
            ))}
          </Space>
        ) : <div role="status">{t("当前没有可选择的面试，请先安排面试后再开始准备。")}</div>}
      </Modal>
      <Modal
        open={pilotReviewChooserOpen}
        title={t("选择要复盘的面试")}
        footer={null}
        onCancel={() => {
          setPilotReviewChooserOpen(false);
          setPilotReviewChoices([]);
        }}
      >
        {pilotReviewChoices.length > 0 ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            {pilotReviewChoices.map((choice) => (
              <Button
                key={choice.eventId}
                block
                disabled={externalTaskBlocked || !applicationTaskResolution.tasks.some(
                  (task) => task.taskId === 'application.interview_review'
                    && task.ref.eventId === choice.eventId
                    && task.executable,
                )}
                onClick={() => {
                  const task = applicationTaskResolution.tasks.find(
                    (candidate) => candidate.taskId === 'application.interview_review'
                      && candidate.ref.eventId === choice.eventId,
                  );
                  if (!task?.executable) {
                    message.warning(liveText(() => t("该面试尚无可用复盘，请先确认事件已完成。")));
                    return;
                  }
                  const result = launchTask({
                    ref: {
                      taskId: 'application.interview_review',
                      applicationId: application.id,
                      eventId: choice.eventId,
                    },
                    source: 'interview_event_card',
                    focus: 'current',
                  });
                  if (taskLaunchAccepted(result)) {
                    setEditingNote(noteRecords.find((note) => note.application_event_id === choice.eventId) ?? null);
                    setPilotReviewChooserOpen(false);
                    setPilotReviewChoices([]);
                  }
                }}
              >
                {choice.label}
              </Button>
            ))}
          </Space>
        ) : <div role="status">{t("当前没有可复盘的面试，请先完成或安排面试后再试。")}</div>}
      </Modal>
      <Modal
        open={jdEditorOpen}
        title={t("投递岗位资料")}
        okText={applicationJdDraft?.resultUnknown ? t("使用原尝试重试") : t("保存岗位资料")}
        cancelText={t("取消")}
        confirmLoading={jdSave.isPending}
        onOk={submitJd}
        onCancel={() => setJdEditorOpen(false)}
      >
        <Input.TextArea
          rows={10}
          value={applicationJdDraft?.jdText ?? applicationJdQuery.data?.current?.jd_text ?? ''}
          disabled={Boolean(applicationJdDraft?.resultUnknown)}
          onChange={(event) => onApplicationJdDraftChange?.(application!.id, { jdText: event.target.value })}
          placeholder={t("粘贴岗位描述")}
        />
        <Input
          style={{ marginTop: 12 }}
          value={applicationJdDraft?.sourceUrl ?? applicationJdQuery.data?.current?.source_url ?? ''}
          disabled={Boolean(applicationJdDraft?.resultUnknown)}
          onChange={(event) => onApplicationJdDraftChange?.(application!.id, { sourceUrl: event.target.value })}
          placeholder={t("来源 URL（仅展示，不会访问）")}
        />
        {applicationJdDraft?.resultUnknown && (
          <Paragraph type="warning" style={{ marginTop: 12, marginBottom: 0 }}>
            {t("保存结果待确认，请使用原尝试重试。")}
          </Paragraph>
        )}
      </Modal>
      <Modal
        open={jdHistoryOpen}
        title={t("岗位资料历史")}
        width={680}
        footer={null}
        onCancel={() => { setJdHistoryOpen(false); setSelectedJdVersion(null); }}
      >
        <div className={styles.jdHistoryList}>
          {jdHistoryQuery.isLoading ? <Spin /> : (jdHistoryQuery.data ?? []).map((version) => (
            <button
              key={version.id}
              type="button"
              className={`${styles.jdHistoryOption} ${selectedJdVersion === version.id ? styles.jdHistoryOptionSelected : ''}`}
              aria-pressed={selectedJdVersion === version.id}
              onClick={() => setSelectedJdVersion(version.id)}
            >
              <span className={styles.jdHistoryMeta}>
                <strong>{t("版本 ")}{version.version_number}</strong>
                <span>{version.source_kind === 'pilot' ? t("Pilot 保存") : t("界面保存")}</span>
                <span>{dayjs(version.created_at).format('YYYY-MM-DD HH:mm:ss')}</span>
              </span>
              <span className={styles.jdHistoryPreview}>{version.preview.slice(0, 160)}</span>
            </button>
          ))}
          {jdHistoryQuery.isError && <Alert type="error" message={t("历史读取失败")} action={<Button onClick={() => void jdHistoryQuery.refetch()}>{t("重试")}</Button>} />}
          {jdDetailQuery.isError && <Alert type="error" message={t("版本详情读取失败")} action={<Button onClick={() => void jdDetailQuery.refetch()}>{t("重试")}</Button>} />}
          {!jdHistoryQuery.isLoading && !jdHistoryQuery.isError && (jdHistoryQuery.data ?? []).length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("暂无岗位资料历史")} />
          ) : null}
          {selectedJdVersion !== null && jdDetailQuery.data && (
            <div className={styles.jdHistoryDetail}>
              <div>{t("版本 ")}{jdDetailQuery.data.version_number} · {jdDetailQuery.data.source_kind === 'pilot' ? t("Pilot 保存") : t("界面保存")}</div>
              <div>{t("保存时间：")}{dayjs(jdDetailQuery.data.created_at).format('YYYY-MM-DD HH:mm:ss')}</div>
              <div>{t("本版来源：")}{jdDetailQuery.data.source_url || t("未填写")}</div>
              {jdDetailQuery.data.jd_text}
            </div>
          )}
        </div>
      </Modal>
      <section className={styles.detailWorkspace} {...applicationDragBinding}>
        <div className={styles.header}>
          <Button type="link" className={styles.backButton} icon={<ArrowLeftOutlined />} onClick={closeDetail}>{t("返回上一层")}</Button>
          <div className={styles.titleRow}>
            <div className={styles.stageIdentity}>
              <Title level={3} className={styles.title}>
                {application.company_name} · {application.position_name}
              </Title>
              <Space wrap>
              <Tag color={stageDataBlocked ? 'orange' : 'green'}>{stageLabel}</Tag>
                <Text type="secondary">
                  {application.first_applied_at
                    ? t("投递时间：{value}", { value: formatWorkspaceDate(application.first_applied_at) })
                    : t("记录时间：{value}", { value: formatWorkspaceDate(application.created_at) })}
                </Text>
                <Text type="secondary">{t("下一步时间：")}{eventsQuery.isLoading
                    ? t("读取中")
                    : eventsQuery.isError
                      ? t("暂时无法读取")
                      : upcomingEvent
                        ? dayjs(upcomingEvent.scheduled_at).format(t("M 月 D 日 HH:mm"))
                        : t("待安排")}
                </Text>
              </Space>
            </div>
            <Space className={styles.headerActions} wrap>
              <Button
                type="primary"
                size="large"
                data-testid="application-stage-action"
                disabled={externalTaskBlocked
                  || (stageDataBlocked && !stageDataHasError)
                  || (application.status === 'interview' && !stageDataBlocked && headerPrimaryTask === null)}
                onClick={(event) => {
                  if (stageDataBlocked) {
                    retryStageData();
                    return;
                  }
                  runStageAction(event.currentTarget);
                }}
              >
                {stagePrimaryActionLabel}
              </Button>
              <Dropdown menu={{ items: moreActionItems }} trigger={['click']}>
                <Button
                  data-testid="application-more-actions"
                  size="large"
                  icon={<MoreOutlined />}
                >{t("更多操作")}</Button>
              </Dropdown>
            </Space>
          </div>
        </div>

        <div className={styles.tabList} role="tablist" aria-label={t("投递详情分段")}>
          {DETAIL_TABS.map((tab, index) => (
            <button
              key={tab.id}
              ref={(element) => { tabRefs.current[index] = element; }}
              type="button"
              role="tab"
              id={`application-${tab.id}-tab`}
              aria-selected={activeTab === tab.id}
              aria-controls={`application-${tab.id}-panel`}
              tabIndex={activeTab === tab.id ? 0 : -1}
              className={`${styles.tab} ${activeTab === tab.id ? styles.tabActive : ''}`}
              onClick={() => setActiveTab(tab.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div
          id="application-overview-panel"
          role="tabpanel"
          aria-labelledby="application-overview-tab"
          tabIndex={0}
          hidden={activeTab !== 'overview'}
          className={styles.tabPanel}
        >
          <section className={`${styles.workspaceSection} ${styles.overviewSection}`} aria-labelledby="application-overview-heading">
            <Title id="application-overview-heading" level={4} className={styles.workspaceSectionTitle}>{t("概览")}</Title>
            <div className={styles.summaryGrid}>
              <div className={styles.summaryCard}>
                <Text type="secondary">{t("当前阶段")}</Text>
                <Text strong>{stageLabel}</Text>
              </div>
              <div className={styles.summaryCard}>
                <Text type="secondary">{t("下一时间")}</Text>
                <Text strong>
                  {eventsQuery.isLoading
                    ? t("读取中")
                    : eventsQuery.isError
                      ? t("暂时无法读取")
                      : upcomingEvent
                        ? formatWorkspaceDate(upcomingEvent.scheduled_at)
                        : t("待安排")}
                </Text>
              </div>
              <div className={styles.summaryCard}>
                <Text type="secondary">{t("最近变化")}</Text>
                <Text strong>{formatWorkspaceDate(application.updated_at, t("暂无更新"))}</Text>
                <Text type="secondary">{t("投递记录已更新")}</Text>
              </div>
            </div>
            {eventsQuery.isError ? (
              <Alert type="warning" showIcon message={t("日程暂时无法读取")} action={<Button size="small" onClick={() => void eventsQuery.refetch()}>{t("重试")}</Button>} />
            ) : null}
            <div className={styles.overviewBlock}>
              <Text strong>{t("JD 摘要")}</Text>
              {applicationJdQuery.isLoading ? <Spin size="small" /> : applicationJdQuery.isError ? (
                <Alert type="warning" showIcon message={t("岗位资料暂时无法读取")} action={<Button size="small" onClick={() => void applicationJdQuery.refetch()}>{t("重试")}</Button>} />
              ) : applicationJdQuery.data?.current ? (
                <Paragraph ellipsis={{ rows: 3 }} className={styles.overviewText}>
                  {applicationJdQuery.data.current.jd_text}
                </Paragraph>
              ) : <Text type="secondary">{t("尚未确认岗位描述")}</Text>}
            </div>
            <div className={styles.overviewBlock}>
              <Text strong>{t("备注")}</Text>
              <Paragraph type="secondary" className={styles.overviewText}>
                {application.notes || t("暂无补充备注")}
              </Paragraph>
            </div>
          </section>

          {nextStepSuggestions && onSetDisposition && onNextStepNavigate && (
            <NextStepSuggestions
              applicationId={application.id}
              suggestions={nextStepSuggestions}
              sessionState={nextStepSessionState}
              onSetDisposition={onSetDisposition}
              onNavigate={onNextStepNavigate}
              isNavigationAvailable={isNavigationAvailable}
              onNavigateReadonly={onNextStepReadonlyNavigate}
              isReadonlyNavigationAvailable={isReadonlyNavigationAvailable}
            />
          )}
        </div>

        <div
          id="application-preparation-panel"
          role="tabpanel"
          aria-labelledby="application-preparation-tab"
          tabIndex={0}
          hidden={activeTab !== 'preparation'}
          className={styles.tabPanel}
        >
          {externalTaskBlocked ? (
              <Alert
                style={{ marginTop: 12 }}
                type="warning"
                showIcon
                message={t("有一项助手操作正在等待处理")}
                description={t("请先在 Pilot 中完成确认；当前投递任务不会替换或暴露其他操作的身份。")}
              />
          ) : null}
          <div className={styles.preparationGrid}>
          <div className={styles.preparationMain}>
          <section className={`${styles.preparationCard} ${styles.jdCard}`} aria-labelledby="application-materials-heading">
            <div className={`${styles.cardHeader} ${styles.jdCardHeader}`}>
              <div className={styles.jdIdentity}>
                <Title id="application-materials-heading" level={4} className={`${styles.cardTitle} ${styles.jdCardTitle}`}><FileTextOutlined aria-hidden="true" />{t("岗位描述")}</Title>
                {currentJd && !applicationJdQuery.isError && !applicationJdQuery.isLoading ? (
                  <Text type="secondary">{t("当前 JD · 版本 ")}{currentJd.version_number}</Text>
                ) : null}
              </div>
              <Space wrap>
                <Button icon={<HistoryOutlined aria-hidden="true" />} onClick={() => { setJdHistoryOpen(true); setSelectedJdVersion(null); }}>{t("查看历史")}</Button>
                <Button icon={<EditOutlined aria-hidden="true" />} disabled={applicationJdQuery.isLoading || applicationJdQuery.isError} onClick={startJdEditor}>
                  {applicationJdQuery.isLoading ? t("读取中") : applicationJdQuery.isError ? t("暂不可编辑") : currentJd ? t("更新 JD") : t("添加 JD")}
                </Button>
              </Space>
            </div>
            {applicationJdQuery.isLoading ? <Spin size="small" /> : applicationJdQuery.isError ? (
              <Alert type="warning" showIcon message={t("岗位资料暂时无法读取")} action={<Button onClick={() => void applicationJdQuery.refetch()}>{t("重试")}</Button>} />
            ) : applicationJdQuery.data === undefined ? (
              <Text type="secondary">{t("岗位资料状态尚未加载")}</Text>
            ) : currentJd ? (
              <>
                <div id="application-jd-text" className={`${styles.jdText} ${longJd && !jdExpanded ? styles.jdTextCollapsed : ''}`}>
                  <JobDescriptionContent text={currentJd.jd_text} />
                </div>
                {longJd ? (
                  <Button type="link" className={styles.inlineAction} aria-expanded={jdExpanded} aria-controls="application-jd-text" onClick={() => setExpandedJdId(jdExpanded ? null : currentJd.id)}>
                    {jdExpanded ? t("收起全文") : t("展开全文")}
                  </Button>
                ) : null}
                {jdSource ? (
                  <div className={styles.jdSource}>
                    <Text type="secondary">{t("来源：")}{jdSource}</Text>
                    <Button onClick={async () => {
                      try {
                        if (!navigator.clipboard) throw new Error('clipboard unavailable');
                        await navigator.clipboard.writeText(jdSource);
                        message.success(liveText(() => t("来源已复制")));
                      } catch { message.error(liveText(() => t("无法复制，请手动选择来源文字"))); }
                    }}>{t("复制来源")}</Button>
                  </div>
                ) : null}
              </>
            ) : <Text type="secondary">{t("尚未确认岗位描述")}</Text>}
          </section>

          <section className={styles.preparationCard} aria-labelledby="application-linked-materials-heading" data-task-id={materialTask?.taskId} data-primary={materialTask?.primary ? 'true' : undefined}>
            <div className={styles.cardHeader}>
              <Title id="application-linked-materials-heading" level={4} className={styles.cardTitle}>{t("关联材料")}</Title>
              <Button disabled={materialEntryBlocked} onClick={() => {
                if (materialEntryBlocked) return;
                if (materialTask) launchResolvedTask(materialTask);
                else launchMaterialKit(currentJd ? { jdSnapshot: currentJd.jd_text, jdVersionID: currentJd.id } : {});
              }}>{materialActionLabel}</Button>
            </div>
            {materialKitQuery.isLoading ? <div role="status"><Spin size="small" />{t(" 正在读取投递材料")}</div> : materialKitQuery.isError ? (
              <Alert type="warning" showIcon message={t("投递材料暂时无法读取")} action={<Button onClick={() => void materialKitQuery.refetch()}>{t("重试")}</Button>} />
            ) : materialOwnerMismatch ? (
              <Alert type="warning" showIcon message={t("材料归属暂不可确认")} action={<Button onClick={() => void materialKitQuery.refetch()}>{t("重试")}</Button>} />
            ) : materialKit ? (
              <div className={styles.materialSummary}>
                <div className={styles.linkedResume}>
                  <Text type="secondary">{t("关联简历")}</Text>
                  <Text strong>
                    {resumesLoading ? t("正在读取关联简历") : resumesError ? t("关联简历暂时无法读取")
                      : linkedResume ? linkedResume.title || linkedResume.name || t("未命名简历")
                        : materialKit.resume_id ? t("关联简历当前不可用") : t("尚未关联简历")}
                  </Text>
                  <Text type="secondary">{t("仅表示材料包关联，不代表提交时的简历快照。")}</Text>
                </div>
                <div className={styles.materialMeta}>
                  <Text>{materialKit.status === 'submitted' ? t("旧投递标记，缺少证据快照") : materialKit.status === 'ready' ? t("材料已就绪") : materialKit.status === 'draft' ? t("准备草稿") : t("材料状态待核对")}</Text>
                  <Text type="secondary">{t("最近保存：")}{formatWorkspaceDate(materialKit.updated_at)}</Text>
                </div>
                {currentJd && materialKit.jd_version_id && materialKit.jd_version_id !== currentJd.id ? (
                  <Text type="warning">{t("材料使用的 JD 与当前版本不同，请在材料工作区核对来源。")}</Text>
                ) : null}
              </div>
            ) : materialKitQuery.data === undefined ? (
              <Text type="secondary">{t("投递材料状态尚未加载")}</Text>
            ) : (
              <Paragraph type="secondary" className={styles.compactCopy}>
                {application.status === 'pending' ? t("尚未保存投递材料。进入准备工作区选择简历，核对岗位要求与提交前检查。") : t("尚未保存投递材料。可在材料工作区选择关联简历，补充本次投递的材料记录。")}
              </Paragraph>
            )}
            {materialTask && !materialTask.executable ? <Text type="secondary">{t("请先完成必要资料读取与核对，再打开材料。")}</Text> : null}
          </section>

          <section className={styles.preparationCard} aria-labelledby="application-preparation-heading">
            <Title id="application-preparation-heading" level={4} className={styles.cardTitle}>{application.status === 'pending' ? t("准备任务") : t("后续准备")}</Title>
            <div className={styles.taskList}>
              {remainingPreparationTasks.length > 0 ? remainingPreparationTasks.map((task) => {
                const baseCopy = TASK_COPY[task.taskId] ?? { title: t("当前任务"), description: t("当前任务状态已更新。"), action: t("查看任务") };
                const copy = task.taskId === 'application.interview_prepare' ? { ...baseCopy, action: t("准备这场面试") }
                  : task.taskId === 'application.interview_review' ? { ...baseCopy, action: task.reason === 'interview_review_available' ? t("查看复盘") : t("开始复盘") }
                    : baseCopy;
                const taskEvent = task.ref.eventId === undefined ? undefined : allEvents.find((event) => event.id === task.ref.eventId);
                const unavailable = externalTaskBlocked || !task.executable || task.availability === 'loading' || task.availability === 'blocked' || task.availability === 'unavailable';
                return (
                  <div
                    className={styles.taskCard}
                    key={`${task.taskId}:${task.ref.eventId ?? task.ref.applicationId}`}
                    data-task-id={task.taskId}
                    data-primary={task.primary ? 'true' : undefined}
                  >
                    <div>
                      <Text strong>{copy.title}</Text>
                      {taskEvent ? <Paragraph type="secondary">{eventSubtypeLabel(taskEvent.subtype)} · {formatWorkspaceDate(taskEvent.scheduled_at, t("时间待确认"))}</Paragraph> : null}
                      <Paragraph type="secondary">{copy.description}</Paragraph>
                      <Text type="secondary">
                        {task.availability === 'loading' ? t("正在读取状态")
                          : task.availability === 'unavailable' ? t("当前资料暂不可用")
                            : task.availability === 'blocked' ? t("还缺少必要资料")
                               : task.availability === 'waiting_confirmation' ? t("等待确认")
                                : task.reason === 'result_unknown' ? t("结果待确认") : ''}
                      </Text>
                    </div>
                    <Button
                      size="small"
                      type="default"
                      disabled={unavailable}
                      onClick={() => launchResolvedTask(task)}
                    >
                      {copy.action}
                    </Button>
                  </div>
                );
              }) : (
                <Text type="secondary">{t("暂无其他准备任务。收到笔试或面试通知后，可添加日程并从对应事件进入准备。")}</Text>
              )}
            </div>
            {applicationTaskResolution.hasLoading && <div role="status">{t("任务状态正在读取")}</div>}
            {applicationTaskResolution.hasUnavailable && !applicationTaskResolution.hasLoading && (
              <div role="alert">{t("部分任务暂不可用，请先检查相关资料")}</div>
            )}
          </section>
          </div>
          <aside className={styles.preparationAside} aria-label={t("跟进与面试安排")}>
        <section ref={scheduleSectionRef} className={styles.preparationCard} aria-labelledby="application-schedule-heading">
        <div className={styles.cardHeader}>
          <Title id="application-schedule-heading" level={4} className={styles.cardTitle}>
            <CalendarOutlined />{t(" 跟进与安排")}</Title>
          <Button
            data-testid="application-schedule-create"
            icon={<PlusOutlined />}
            onClick={(event) => openScheduleForm(event.currentTarget, 'schedule')}
          >{t("安排日程")}</Button>
        </div>
        {eventsQuery.isLoading ? (
          <div style={{ textAlign: 'center', padding: 16 }}>
            <Spin />
          </div>
        ) : eventsQuery.isError ? (
          <Alert style={{ marginBottom: 16 }} type="warning" showIcon message={t("日程暂时无法读取")} action={<Button size="small" onClick={() => void eventsQuery.refetch()}>{t("重试")}</Button>} />
        ) : eventsQuery.data !== undefined && !eventRowsAreObjects ? (
          <Alert style={{ marginBottom: 16 }} type="warning" showIcon message={t("日程数据格式异常，请重试")} action={<Button size="small" onClick={() => void eventsQuery.refetch()}>{t("重试")}</Button>} />
          ) : eventsQuery.data === undefined ? (
          <Text type="secondary">{t("日程状态尚未加载")}</Text>
        ) : allEvents.length > 0 ? (
          <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
            {visibleScheduleEvents.map((event) => {
              const notesReady = !notesQuery.isLoading && !notesQuery.isError && noteRowsAreObjects;
              const linkedNote = notesReady ? noteRecords.find((note) => note.application_event_id === event.id) : undefined;
              const projected = taskSnapshot.events.status === 'ready'
                ? taskSnapshot.events.value.find((item) => item.eventId === event.id)
                : undefined;
              const terminalEvent = projected?.lifecycle === 'cancelled';
              const unavailableEvent = !projected
                || projected.lifecycle === 'unknown'
                || projected.bucket === 'unavailable';
              const needsStatusUpdate = projected?.bucket === 'needs_status_update';
              const completedEvent = projected?.lifecycle === 'completed';
              const preparationTask = applicationTaskResolution.tasks.find(
                (task) => task.taskId === 'application.interview_prepare' && task.ref.eventId === event.id,
              );
              const reviewTask = applicationTaskResolution.tasks.find(
                (task) => task.taskId === 'application.interview_review' && task.ref.eventId === event.id,
              );
              return (
              <div key={event.id} className={styles.scheduleSummary}>
                <div className={styles.scheduleMeta}>
                  <Text strong>{EVENT_TYPE_LABELS[event.event_type]}</Text>
                  <Text type="secondary">{formatWorkspaceDate(event.scheduled_at, t("时间待确认"))}</Text>
                </div>
                <div className={styles.scheduleDetail}>
                  {Number.isFinite(event.duration_minutes) && event.duration_minutes > 0
                    ? t("时长 {duration_minutes} 分钟", { duration_minutes: event.duration_minutes })
                    : t("时长未设置")}{event.location ? ` · ${event.location}` : ''}{terminalEvent ? ` · ${eventStatusLabel(event.status)}` : ''}
                </div>
                {event.event_type === 'interview' && terminalEvent ? (
                  <Text type="secondary">{t("该面试已结束或取消，暂不提供准备与复盘操作。")}</Text>
                ) : event.event_type === 'interview' && unavailableEvent ? (
                  <Text type="secondary">{t("该面试状态暂不可用，请先刷新日程后再操作。")}</Text>
                ) : event.event_type === 'interview' && needsStatusUpdate ? (
                  <Text type="secondary">{t("该面试状态需要先更新，暂不提供准备与复盘操作。")}</Text>
                ) : event.event_type === 'interview' && !notesReady ? (
                  <Text type="secondary">{t("面试复盘暂不可用，请先完成读取或重试。")}</Text>
                ) : event.event_type === 'interview' && (
                  <Space size={8} wrap>
                    {completedEvent && <Button
                      size="small"
                      type="link"
                      disabled={externalTaskBlocked || !reviewTask?.executable}
                      onClick={() => {
                        if (externalTaskBlocked || !reviewTask?.executable) return;
                        const result = launchTask({
                          ref: reviewTask.ref,
                          source: 'interview_event_card',
                          focus: 'current',
                        });
                        if (taskLaunchAccepted(result)) setEditingNote(linkedNote ?? null);
                      }}
                    >
                      {linkedNote ? t("查看复盘") : t("记录复盘")}
                    </Button>}
                    {completedEvent && linkedNote && (
                      <Button size="small" type="link" onClick={() => openKnowledgeCapture(linkedNote)}>{t("保存为复盘沉淀")}</Button>
                    )}
                    {!completedEvent && preparationTask?.executable && (
                      <Button
                        size="small"
                        type="link"
                        disabled={externalTaskBlocked}
                        onClick={() => launchTask({
                          ref: {
                            taskId: 'application.interview_prepare',
                            applicationId: application.id,
                            eventId: event.id,
                          },
                          source: 'interview_event_card',
                          focus: 'current',
                        })}
                      >{t("面试准备建议")}</Button>
                    )}
                  </Space>
                )}
              </div>
              );
            })}
          </Space>
        ) : (
          <Paragraph type="secondary" className={styles.compactCopy}>{t("尚未添加日程。收到笔试或面试通知后，可在这里记录时间与地点。")}</Paragraph>
        )}
        {!eventsQuery.isLoading && !eventsQuery.isError && allEvents.length > 3 ? (
          <Button type="link" className={styles.inlineAction} aria-expanded={expandedScheduleApplicationId === application.id} onClick={() => setExpandedScheduleApplicationId(expandedScheduleApplicationId === application.id ? null : application.id)}>
            {expandedScheduleApplicationId === application.id ? t("收起日程") : t("查看全部 {length} 条日程", { length: allEvents.length })}
          </Button>
        ) : null}
        <Text type="secondary" className={styles.reminderNote}>{t("网页提醒不发送系统通知；桌面端可在设置中主动开启支持范围内的本地提醒，不会向招聘方发送消息。")}</Text>
        </section>
        <section className={styles.preparationCard} aria-labelledby="application-interview-heading">
        <Title id="application-interview-heading" level={4} className={styles.cardTitle}>{t("面试复盘")}</Title>

        {canonicalInterviewChoices.review.length > 0 ? <Button
          icon={<PlusOutlined />}
          style={{ marginBottom: 16 }}
          onClick={() => {
            if (!interviewReviewDataReady) return;
            setPilotReviewChoices(canonicalInterviewChoices.review);
            setPilotReviewChooserOpen(true);
          }}
          disabled={!interviewReviewDataReady || externalTaskBlocked}
        >{t("选择面试并开始复盘")}</Button> : null}

        {notesQuery.isLoading ? (
          <div style={{ textAlign: 'center', padding: 24 }}>
            <Spin />
          </div>
        ) : notesQuery.isError ? (
          <Alert type="warning" showIcon message={t("面试复盘暂时无法读取")} action={<Button size="small" onClick={() => void notesQuery.refetch()}>{t("重试")}</Button>} />
        ) : notesQuery.data !== undefined && !noteRowsAreObjects ? (
          <Alert type="warning" showIcon message={t("面试复盘数据格式异常，请重试")} action={<Button size="small" onClick={() => void notesQuery.refetch()}>{t("重试")}</Button>} />
        ) : notesQuery.data === undefined ? (
          <Text type="secondary">{t("面试复盘状态尚未加载")}</Text>
        ) : noteRecords.length > 0 ? (
          <Timeline
            items={noteRecords.map((n) => ({
              color: 'green',
              children: (
                <div
                  key={n.id}
                  style={{ paddingBottom: 8, borderBottom: '1px solid #f0f0f0' }}
                >
                  <div className={styles.reviewHeader}>
                    <Text strong>
                      {n.round || t("未标注轮次")} · {n.date}{t(" · 心情 ")}{n.mood || '—'}
                    </Text>
                    <Space size={8} wrap>
                      <Button
                        type="text"
                        size="small"
                        onClick={() => launchNoteReview(n)}
                      >{t("编辑")}</Button>
                      <Button
                        type="text"
                        size="small"
                        onClick={() => launchNoteReview(n)}
                      >{t("复盘建议")}</Button>
                      <Button type="text" size="small" onClick={() => openKnowledgeCapture(n)}>{t("保存为复盘沉淀")}</Button>
                      <Popconfirm
                        title={t("删除这条复盘？")}
                        onConfirm={() => removeNoteMut.mutate(n.id)}
                        okText={t("删除")}
                        cancelText={t("取消")}
                      >
                        <Button type="text" size="small" danger>{t("删除")}</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                  {n.questions && (
                    <div style={{ marginTop: 4 }}>
                      <Text type="secondary">{t("问题：")}</Text>
                      {n.questions}
                    </div>
                  )}
                  {n.self_reflection && (
                    <div>
                      <Text type="secondary">{t("反思：")}</Text>
                      {n.self_reflection}
                    </div>
                  )}
                  {n.difficulty_points && (
                    <div>
                      <Text type="secondary">{t("难点：")}</Text>
                      {n.difficulty_points}
                    </div>
                  )}
                </div>
              ),
            }))}
          />
        ) : (
          <Paragraph type="secondary" className={styles.compactCopy}>
            {canonicalInterviewChoices.review.length > 0 ? t("尚未保存面试复盘，可选择已完成的面试开始记录。") : t("尚无可复盘的面试。完成面试并更新事件状态后，再记录问题与反思。")}
          </Paragraph>
        )}
        </section>
        <section className={styles.resultSummary} aria-label={t("投递结果")}>
          {application.status === 'offer' || application.status === 'closed' ? <>
          <Title id="application-result-heading" level={4} className={styles.cardTitle}>{t("结果")}</Title>
          <Text type="secondary">
            {application.status === 'offer'
              ? t("已进入 Offer 阶段，可通过顶部主操作查看事实、截止时间和待确认信息。")
              : application.status === 'closed'
                ? t("该投递已结束，结果与经验记录保留在投递事实中。")
                : t("尚未进入结果阶段，后续状态会继续在这里汇总。")}
          </Text></> : null}
          <div>
            <Button
              type="link"
              onClick={() => launchTask({
                ref: { taskId: 'application.record_outcome', applicationId: application.id },
                source: 'application_task_card',
                focus: 'current',
              })}
            >{t("投递事实与结果")}</Button>
          </div>
        </section>
          </aside>
          </div>
        </div>

        <div
          id="application-progress-panel"
          role="tabpanel"
          aria-labelledby="application-progress-tab"
          tabIndex={0}
          hidden={activeTab !== 'progress'}
          className={styles.tabPanel}
        >
          <section className={`${styles.workspaceSection} ${styles.progressSection}`} aria-labelledby="application-progress-heading">
            <Title id="application-progress-heading" level={4} className={styles.workspaceSectionTitle}>{t("进展")}</Title>
            <Text type="secondary" className={styles.readOnlyNotice}>{t("进展时间线只汇总现有投递、事件、面试复盘与归属 Offer，不会修改投递状态。")}</Text>
            {eventsQuery.isError ? (
              <Alert type="warning" showIcon message={t("部分日程进展暂时无法读取")} action={<Button size="small" onClick={() => void eventsQuery.refetch()}>{t("重试")}</Button>} />
            ) : null}
            {notesQuery.isError ? (
              <Alert type="warning" showIcon message={t("部分复盘进展暂时无法读取")} action={<Button size="small" onClick={() => void notesQuery.refetch()}>{t("重试")}</Button>} />
            ) : null}
            {offersError ? (
              <Alert type="warning" showIcon message={t("部分 Offer 进展暂时无法读取")} action={onRetryOffers ? <Button size="small" onClick={onRetryOffers}>{t("重试")}</Button> : undefined} />
            ) : null}
            {progressItems.length > 0 ? (
              <div className={styles.progressTimeline} role="list" aria-label={t("进展时间线")}>
                {progressItems.map((item) => (
                  <article key={item.id} className={styles.progressItem} role="listitem">
                    <span className={styles.progressMarker} aria-hidden="true" />
                    <div className={styles.progressBody}>
                      <div className={styles.progressHeader}>
                        <Text strong>{item.title}</Text>
                        <Text type="secondary">{formatWorkspaceDate(item.timestamp)}</Text>
                      </div>
                      {item.detail && <Paragraph type="secondary" className={styles.progressDetail}>{item.detail}</Paragraph>}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <Empty description={t("暂无进展记录")} />
            )}
          </section>
        </div>
      </section>

      {activeTask ? (
        <CoreTaskSurfaceHost
          controller={effectiveTaskController}
          renderOwner={renderTaskOwner}
          heading={t("当前任务")}
          revealOnOpen
          closeGuard={(closingActive) => closingActive.generation === effectiveTaskController.getState().active?.generation
            ? localTaskSurfaceGuardRef.current
            : { pending: true, unsaved: true }}
        />
      ) : null}

      {knowledgeCaptureOpen && editingNote ? (
        <InterviewKnowledgeCaptureDrawer
          open
          note={editingNote}
          draft={interviewKnowledgeCaptureDrafts?.[editingNote.id] ?? createInterviewKnowledgeCaptureDraft()}
          onDraftChange={(draft) => onInterviewKnowledgeCaptureDraftChange?.(editingNote.id, draft)}
          onClose={() => {
            setKnowledgeCaptureOpen(false);
            setEditingNote(null);
          }}
        />
      ) : null}

    </>
  );
}
