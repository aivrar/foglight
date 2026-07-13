import { normalizeWatchRegions } from './watch-model.js';

const DEFAULT_NOTIFICATION_KINDS = [
  'earthquake', 'weather_alert', 'tropical_cyclone', 'tsunami', 'volcano',
  'wildfire', 'natural_event', 'disaster', 'disaster_declaration', 'conflict_report', 'humanitarian_report',
  'aircraft', 'aviation_hazard', 'marine_observation', 'water_level',
  'fireball', 'space_weather', 'orbital_position', 'market_snapshot',
  'technology_activity', 'news_item',
];

export function normalizeInitialSettings(
  body,
  { panelIds, defaultVisible, tvChannelIds, fallbackTvChannel = 'aljazeera' },
) {
  const panels = { ...(body?.panels || {}) };
  for (const panelId of panelIds) {
    if (panels[panelId] == null) panels[panelId] = defaultVisible.has(panelId);
  }
  const requestedChannel = body?.tv_channel;
  const wallInterval = Number(body?.wall_display?.interval_seconds);
  return {
    panels,
    audio: { master: false, ...(body?.audio || {}) },
    tvChannel: tvChannelIds.has(requestedChannel) ? requestedChannel : fallbackTvChannel,
    displayMode: ['overview', 'standard', 'command'].includes(body?.display_mode)
      ? body.display_mode : 'overview',
    watchlist: Array.isArray(body?.watchlist) ? body.watchlist.slice() : [],
    annotations: Array.isArray(body?.annotations) ? body.annotations.slice() : [],
    watchRegions: normalizeWatchRegions(body?.watch_regions, body?.watchlist),
    notifications: {
      enabled: false, in_app: true, system: true,
      quiet_start: '22:00', quiet_end: '07:00', minimum_severity: 'Moderate',
      kinds: DEFAULT_NOTIFICATION_KINDS, changes: ['new', 'escalated'],
      ...(body?.notifications || {}),
    },
    notificationState: {
      seen_revision_keys: [], acknowledged_keys: [], snoozed: [],
      ...(body?.notification_state || {}),
    },
    wallDisplay: {
      interval_seconds: [10, 30, 60, 120, 300].includes(wallInterval) ? wallInterval : 30,
    },
  };
}

export function createSettingsPatch(section, value) {
  if (!['panels', 'audio', 'keys'].includes(section)) {
    throw new TypeError(`unsupported settings section: ${section}`);
  }
  return { [section]: value };
}
