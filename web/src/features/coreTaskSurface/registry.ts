import type { CoreTaskId } from './contracts';

export interface CoreTaskOwner {
  readonly ownerId: string;
}

export type CoreTaskRegistry = Readonly<Record<CoreTaskId, CoreTaskOwner>>;

/**
 * The single desktop owner for every registered CoreTaskId. Values are UI owner
 * identifiers only; this registry intentionally has no service or repository
 * dependencies.
 */
export const CORE_TASK_REGISTRY: CoreTaskRegistry = Object.freeze({
  'application.opportunity_fit': Object.freeze({ ownerId: 'application-opportunity-fit' }),
  'application.material_kit': Object.freeze({ ownerId: 'application-material-kit' }),
  'application.interview_prepare': Object.freeze({ ownerId: 'application-interview-prepare' }),
  'application.interview_review': Object.freeze({ ownerId: 'application-interview-review' }),
  'application.general_review': Object.freeze({ ownerId: 'application-general-review' }),
  'application.offer_review': Object.freeze({ ownerId: 'application-offer-review' }),
  'application.record_outcome': Object.freeze({ ownerId: 'application-record-outcome' }),
  'interview.free_practice': Object.freeze({ ownerId: 'interview-free-practice' }),
  'materials.resume': Object.freeze({ ownerId: 'materials-resume' }),
  'materials.story': Object.freeze({ ownerId: 'materials-story' }),
  'materials.reference': Object.freeze({ ownerId: 'materials-reference' }),
} as const);

/**
 * Explicitly records destructive Task 6 entrypoint removals. This is a strict
 * lexical manifest consumed by the baseline gate; it is not a runtime alias
 * and cannot make an old owner reachable again.
 */
export const CORE_TASK_ENTRYPOINT_CUTOVERS = Object.freeze({
  'AppShellContent.preparePilotMaterials': Object.freeze({
    category: 'core_task',
    taskId: 'application.material_kit',
  }),
  'ApplicationDetail.openOpportunityFit': Object.freeze({
    category: 'core_task',
    taskId: 'application.opportunity_fit',
  }),
  'ApplicationDetail.openMaterials': Object.freeze({
    category: 'core_task',
    taskId: 'application.material_kit',
  }),
  'AppShellContent.openPilotInterviewReview': Object.freeze({
    category: 'core_task',
    taskId: 'application.interview_review',
  }),
  'OfferCenterView.openNegotiation': Object.freeze({
    category: 'core_task',
    taskId: 'application.offer_review',
  }),
  'PilotOpportunityFitV2Card': Object.freeze({
    category: 'core_task',
    taskId: 'application.opportunity_fit',
  }),
  'PilotOpportunityFitCard': Object.freeze({
    category: 'core_task',
    taskId: 'application.opportunity_fit',
  }),
  'InterviewReadinessCenter.actionFor': Object.freeze({
    category: 'core_task',
    taskId: 'application.interview_prepare',
  }),
} as const);

export type CoreTaskOwnerLookupResult =
  | { readonly ok: true; readonly ownerId: string }
  | { readonly ok: false; readonly reason: 'task_owner_unavailable' };

const TASK_OWNER_UNAVAILABLE: CoreTaskOwnerLookupResult = Object.freeze({
  ok: false,
  reason: 'task_owner_unavailable',
});

/** Looks up an owner without falling back to another task or legacy surface. */
export function getCoreTaskOwner(taskId: unknown): CoreTaskOwnerLookupResult {
  try {
    if (typeof taskId !== 'string' || !Object.prototype.hasOwnProperty.call(CORE_TASK_REGISTRY, taskId)) {
      return TASK_OWNER_UNAVAILABLE;
    }
    return {
      ok: true,
      ownerId: CORE_TASK_REGISTRY[taskId as CoreTaskId].ownerId,
    };
  } catch {
    return TASK_OWNER_UNAVAILABLE;
  }
}

export const resolveCoreTaskOwner = getCoreTaskOwner;

export interface RetainedOwnerSafety {
  readonly hasDraft: boolean;
  readonly activeRun: boolean;
  readonly pendingApproval: boolean;
}

/** Non-navigable source/interview draft owners join the existing registry for
 * exit/update safety only. They do not create task IDs or alternate launchers. */
export const RETAINED_DRAFT_OWNER_REGISTRY = Object.freeze({
  'application.jd': Object.freeze({ ownerId: 'application-jd-source' }),
  'application.offer_review': CORE_TASK_REGISTRY['application.offer_review'],
  'interview.retained': Object.freeze({ ownerId: 'interview-retained-drafts' }),
});

type RetainedOwnerReadouts = Record<keyof typeof RETAINED_DRAFT_OWNER_REGISTRY, () => RetainedOwnerSafety>;

/** Read at decision time, not at registration time. A failed owner read cannot
 * silently authorize a destructive update or exit. No domain state is stored. */
export function readRetainedOwnerSafety(owners: RetainedOwnerReadouts): RetainedOwnerSafety {
  let hasDraft = false;
  let activeRun = false;
  let pendingApproval = false;
  for (const owner of Object.keys(RETAINED_DRAFT_OWNER_REGISTRY) as (keyof RetainedOwnerReadouts)[]) {
    try {
      const projection = owners[owner]();
      if (typeof projection?.hasDraft !== 'boolean' || typeof projection.activeRun !== 'boolean'
        || typeof projection.pendingApproval !== 'boolean') throw new Error('owner_safety_unavailable');
      hasDraft ||= projection.hasDraft;
      activeRun ||= projection.activeRun;
      pendingApproval ||= projection.pendingApproval;
    } catch {
      hasDraft = true;
      activeRun = true;
      pendingApproval = true;
    }
  }
  return { hasDraft, activeRun, pendingApproval };
}
