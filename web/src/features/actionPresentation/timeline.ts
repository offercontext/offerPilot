import type { PilotPresentationSnapshot, PilotTimelineCache, PilotTimelinePage } from './contracts';

/** Tombstones stay in the cache so a delayed lower revision cannot resurrect content. */
export function applyTimelinePage(cache: PilotTimelineCache | null, page: PilotTimelinePage): PilotTimelineCache {
  if (page.schema_version !== 1 || !Number.isSafeInteger(page.conversation_id) || page.conversation_id <= 0
    || !Number.isSafeInteger(page.high_watermark) || page.high_watermark < 0
    || !['snapshot', 'changes'].includes(page.mode) || !Array.isArray(page.items)
    || typeof page.cursor !== 'string' || !page.cursor
    || (cache && cache.conversation_id !== page.conversation_id)) throw new Error('timeline_contract_mismatch');
  if (cache && cache.high_watermark > page.high_watermark) return cache;
  const items = { ...cache?.items };
  for (const item of page.items) {
    if (item.schema_version !== 1 || item.conversation_id !== page.conversation_id
      || !item.turn_id || !item.item_id.startsWith(`turn:${item.turn_id}:`)
      || ![item.revision, item.display_revision, item.ordinal, item.change_seq].every((value) => Number.isSafeInteger(value) && value > 0)
      || item.change_seq > page.high_watermark || typeof item.deleted !== 'boolean'
      || (!item.deleted && (!item.payload || item.payload.schema_version !== 1 || item.payload.kind !== item.item_type
        || item.item_id !== `turn:${item.turn_id}:${item.payload.item_id}`))
      || (item.deleted && item.payload !== null)) throw new Error('timeline_item_mismatch');
    const existing = items[item.item_id];
    if (existing && item.revision === existing.revision && (
      item.payload_digest !== existing.payload_digest || item.deleted !== existing.deleted
      || item.source_revision !== existing.source_revision || item.ordinal !== existing.ordinal
    )) throw new Error('timeline_revision_conflict');
    if (!existing || item.revision > existing.revision) items[item.item_id] = item;
  }
  return { conversation_id: page.conversation_id, high_watermark: page.high_watermark, cursor: page.cursor, items };
}

export function timelinePresentation(cache: PilotTimelineCache): PilotPresentationSnapshot {
  return {
    schema_version: 1, conversation_id: cache.conversation_id, timeline: cache,
    items: Object.values(cache.items)
      .filter((item) => !item.deleted && item.payload
        // A completed boundary is still durable, but repeating a success line
        // beside every reply adds noise. Uncertainty and interruption stay visible.
        && !(item.item_type === 'run_boundary' && item.source_revision === 'completed'))
      .sort((left, right) => left.ordinal - right.ordinal)
      .map((item) => ({ ...item.payload!, timeline_item_id: item.item_id, turn_id: item.turn_id, revision: item.revision })),
  };
}
