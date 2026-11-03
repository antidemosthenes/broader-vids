export const state = {
  view: "recommendations",
  platform: "youtube",
  exclusionTab: "creators",
  data: { runs: {}, videos: {}, pending_items: {}, pending: {} },
  calibration: [],
  exclusions: { creators: {}, tags: {}, bilibili_sync: {} },
  learning: null,
  selected: new Map(),
  scrolls: new Map(),
  inspectorScrolls: new Map(),
  drafts: new Map(),
  tagDrafts: new Map(),
  search: new Map(),
  busy: new Set(),
  notes: new Map(),
  generation: 0,
  readVersion: 0,
  ready: false,
  reviewOpen: false,
};

export const contextKey = () => `${state.view}:${state.platform}`;
export const draftKey = (id, dimension) => `${id}|${dimension}`;
export const selectedId = () => state.selected.get(contextKey());

export function record(id) {
  const current = state.data.videos[id];
  const calibration = state.calibration.find(
    (entry) => entry.item.canonical_id === id,
  );
  return current && calibration
    ? { ...current, ...calibration }
    : current || calibration || { details: {}, feedback_context: {} };
}

export function entries() {
  if (state.view === "calibration")
    return state.calibration
      .filter((entry) => entry.item.platform === state.platform)
      .map((entry) => ({ ...entry.item, quality_evidence: {}, ...entry }));
  return state.data.runs[state.platform]?.selected || [];
}

export function updateRecord(id, fields) {
  if (state.data.videos[id]) Object.assign(state.data.videos[id], fields);
  const calibration = state.calibration.find(
    (entry) => entry.item.canonical_id === id,
  );
  if (calibration) Object.assign(calibration, fields);
}

export function updateRating(id, dimension, payload) {
  const details = { ...record(id).details };
  if (payload.value === "clear") delete details[dimension];
  else details[dimension] = { ...payload };
  updateRecord(id, { details });
}

export function applyTagRules({ tags, tag_revision = 0 }) {
  if (tag_revision < (state.exclusions.tag_revision || 0)) return;
  state.exclusions.tags = tags;
  state.exclusions.tag_revision = tag_revision;
}

export function applyExclusions(exclusions) {
  const previous = state.exclusions;
  state.exclusions = exclusions;
  if ((previous.tag_revision || 0) > (exclusions.tag_revision || 0))
    Object.assign(state.exclusions, {
      tags: previous.tags,
      tag_revision: previous.tag_revision,
    });
  const update = (entry) => {
    const creator = entry.creator;
    if (!creator) return;
    const rule = exclusions.creators[creator.platform]?.find(
      (rule) => rule.creator_id === creator.creator_id,
    );
    entry.creator = {
      ...creator,
      excluded: !!rule,
      imported: !!rule?.imported,
    };
  };
  Object.values(state.data.videos).forEach(update);
  state.calibration.forEach(update);
}
