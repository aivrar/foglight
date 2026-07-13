export function createAppStore(initial = {}) {
  const listeners = new Set();
  const state = {
    ui: {
      theater: 'global', tvChannel: 'aljazeera', displayMode: 'standard',
      incidentFilter: 'global', panels: {}, ...initial.ui,
    },
    user: {
      watchlist: [], annotations: [], watchRegions: [],
      notifications: { enabled: false },
      notificationState: { seen_revision_keys: [], acknowledged_keys: [], snoozed: [] },
      wallDisplay: { interval_seconds: 30 },
      audio: { master: false }, ...initial.user,
    },
    lifecycle: { settings: null, shuttingDown: false, ...initial.lifecycle },
  };

  function update(section, patch) {
    if (!Object.hasOwn(state, section) || !patch || typeof patch !== 'object') {
      throw new TypeError(`invalid store update for ${section}`);
    }
    Object.assign(state[section], patch);
    for (const listener of listeners) listener(state, section);
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  return Object.freeze({ state, update, subscribe });
}
