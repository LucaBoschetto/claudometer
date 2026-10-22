// Tests for the reset-marker traces in web.py.
//
// Run: ./scripts/run_tests.sh   (regenerates shipped.mjs from web.py first)
//
// The legend persistence (syncResetMarkerVisibility) reads chartEl.data[i] and
// pairs it with RESET_MARKER_SERIES[i], and renderChart keeps that alignment by
// putting these traces first in the array. So the trace count and their order
// are load-bearing, not cosmetic: drop an empty trace or reorder them and the
// wrong series gets hidden. These tests pin that down.

import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildResetTraces, RESET_MARKER_SERIES, setResetMarkerShown,
} from './shipped.mjs';

// toLocalPlotTs renders in local time, so build expectations the same way rather
// than hardcoding a string that only matches in one timezone.
const localTs = (y, mo, d, h, mi) => {
  const p = (n) => String(n).padStart(2, '0');
  return `${y}-${p(mo)}-${p(d)} ${p(h)}:${p(mi)}:00`;
};
const at = (y, mo, d, h, mi) => new Date(y, mo - 1, d, h, mi, 0).toISOString();

const row = (overrides) => ({ ts: at(2026, 7, 16, 12, 0), ...overrides });

test.beforeEach(() => {
  RESET_MARKER_SERIES.forEach((s) => setResetMarkerShown(s.key, true));
});

test('emits one trace per kind, in RESET_MARKER_SERIES order', () => {
  const traces = buildResetTraces([row({ session_resets: at(2026, 7, 16, 5, 0) })]);
  assert.equal(traces.length, RESET_MARKER_SERIES.length);
  assert.deepEqual(traces.map((t) => t.name), ['Session resets', 'Weekly resets']);
});

test('still emits every trace when no rows carry a reset', () => {
  // An omitted trace would shift the other one's index out from under the
  // persistence layer, and make its legend entry vanish.
  const traces = buildResetTraces([row({}), row({})]);
  assert.equal(traces.length, RESET_MARKER_SERIES.length);
  assert.deepEqual(traces.map((t) => t.x.length), [0, 0]);
});

test('still emits every trace for no rows at all', () => {
  assert.equal(buildResetTraces([]).length, RESET_MARKER_SERIES.length);
});

test('a reset draws as one vertical segment, null-separated', () => {
  const [session] = buildResetTraces([row({ session_resets: at(2026, 7, 16, 5, 0) })]);
  const ts = localTs(2026, 7, 16, 5, 0);
  assert.deepEqual(session.x, [ts, ts, null]);
  assert.deepEqual(session.y, [0, 100, null]);
});

test('repeated resets across rows are drawn once', () => {
  // Every row in a session carries the same session_resets value, so without
  // dedup a run of rows would stack thousands of lines on one timestamp.
  const reset = at(2026, 7, 16, 5, 0);
  const [session] = buildResetTraces([
    row({ session_resets: reset }), row({ session_resets: reset }), row({ session_resets: reset }),
  ]);
  assert.equal(session.x.length, 3);
});

test('distinct resets each get a segment', () => {
  const [session] = buildResetTraces([
    row({ session_resets: at(2026, 7, 16, 5, 0) }),
    row({ session_resets: at(2026, 7, 16, 10, 0) }),
  ]);
  assert.equal(session.x.length, 6);
  assert.deepEqual(session.x.slice(0, 2), [localTs(2026, 7, 16, 5, 0), localTs(2026, 7, 16, 5, 0)]);
  assert.deepEqual(session.x.slice(3, 5), [localTs(2026, 7, 16, 10, 0), localTs(2026, 7, 16, 10, 0)]);
});

test('the two kinds do not bleed into each other', () => {
  const [session, weekly] = buildResetTraces([
    row({ session_resets: at(2026, 7, 16, 5, 0), weekly_resets: at(2026, 7, 14, 0, 0) }),
  ]);
  assert.equal(session.x[0], localTs(2026, 7, 16, 5, 0));
  assert.equal(weekly.x[0], localTs(2026, 7, 14, 0, 0));
});

test('rows with no reset value are skipped', () => {
  const [session] = buildResetTraces([
    row({ session_resets: null }),
    row({ session_resets: '' }),
    row({}),
    row({ session_resets: at(2026, 7, 16, 5, 0) }),
  ]);
  assert.equal(session.x.length, 3);
});

test('stored visibility maps onto the trace', () => {
  setResetMarkerShown('session_resets', false);
  const [session, weekly] = buildResetTraces([row({ session_resets: at(2026, 7, 16, 5, 0) })]);
  assert.equal(session.visible, 'legendonly');
  assert.equal(weekly.visible, true);
});

test('hidden markers still carry their points, so the legend can bring them back', () => {
  setResetMarkerShown('session_resets', false);
  const [session] = buildResetTraces([row({ session_resets: at(2026, 7, 16, 5, 0) })]);
  assert.equal(session.x.length, 3);
});
