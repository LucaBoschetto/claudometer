# Hideable reset markers — design

## Summary

Make the vertical reset markers hideable from the chart legend, the same way
`Current session`, `Weekly`, and the rest already are. They get two legend
entries — `Session resets` (blue) and `Weekly resets` (orange) — and the
hidden/shown choice survives a page reload.

At `range=all` the blue session markers fire roughly every five hours across
months of history, so they collapse into a dense picket fence that hides the
data behind it. They stay useful when zoomed in, so the fix is a toggle, not a
removal.

## Why they aren't already hideable

Everything hideable in that legend is a **trace**. The reset markers are
**shapes** (`buildShapes`, web.py), which are layout-level annotations painted
onto the canvas. Plotly's legend enumerates traces only, so a shape can never
appear there. No amount of legend configuration changes this — the markers have
to stop being shapes.

## Approach

Replace `buildShapes(rows)` with `buildResetTraces(rows)`, returning two scatter
traces. A vertical line at time `t` is the two-point segment `x=[t, t]`,
`y=[0, 100]`; independent segments are chained into one trace by separating them
with `null`, which Plotly renders as a break rather than a connecting line. So
each reset *kind* is one trace with one legend entry, and legend toggling then
works natively with no toggle code of our own.

Same dedup as today (first occurrence of each distinct local timestamp wins),
same colors, same `width: 1, dash: 'dot'`.

### Trace order vs. legend order

The two reset traces are placed **first** in the traces array (indices 0 and 1)
and pushed to the end of the legend with `legendrank`. This decoupling is
already how the file works — `expectedSession` is pushed last but ranked 5.

Fixed indices matter for correctness, not tidiness. `Sonnet only` and the two
expected traces are conditional, so appending the reset traces would let their
indices shift mid-session. Plotly aligns old and new traces **by index** when
preserving UI state under `uirevision`, so a shift would apply a preserved
visibility to whichever trace inherited the slot — hiding the wrong series.

For the same reason both traces are **always** emitted, with empty `x`/`y` when
a range contains no resets of that kind. An omitted trace would shift the other
one's index and make the legend entry appear and disappear as the range changes.

### Persistence

`uirevision: 'keep-zoom'` carries a toggle across an auto-refresh and a range
switch, but not a page reload. So `syncResetMarkerVisibility` mirrors the state
into `localStorage` under `tracker_show_session_resets` /
`tracker_show_weekly_resets`, following the existing `tracker_view_mode` pattern
and its `storageGet`/`storageSet` helpers. On load the stored value seeds each
trace's `visible` (`true` or `'legendonly'`). Default is visible, so first load
is unchanged from today.

The sync **observes** both traces on the chart rather than deriving the new
state from the click that caused it. This matters more than it looks:
`uirevision` protects a user's toggle only while the `visible` we supply stays
unchanged. Once our stored state and the chart's disagree, the next refresh
supplies the stale value and overrides the user.

Deriving invites exactly that drift, because a legend click is not one clean
event:

- `plotly_legendclick` fires **before** the toggle applies, and a single click
  doesn't apply until Plotly has waited out its double-click delay.
- A double-click fires `legendclick` **twice**, both carrying the *pre-toggle*
  value, then isolates the trace via `legenddoubleclick`.
- Double-clicking any *other* legend entry isolates it and hides both reset
  traces without either reset entry being clicked at all.

Reading `chartEl.data` back on **`plotly_restyle`** — the event Plotly emits once
a visibility change has actually landed — covers all three, since it cares only
about where the chart ended up, not how it got there. Persisting an isolate
means the markers stay hidden after a reload; that is the honest reading of
"the user just hid these", and it keeps supplied and on-screen states equal.

### The empty-DB branch uses react, not newPlot

`renderChart([])` must not call `Plotly.newPlot`: it purges the div, which drops
every handler `ensureChartBindings` installed, and the `hasBoundChartEvents`
guard then refuses to reinstall them. Against an empty DB the first render binds
and the next purges, leaving legend persistence *and* zoom handling dead for the
rest of the session even once samples arrive. `Plotly.react` never purges, and
matches what `renderBurnCharts` already does.

## Deliberate behavior changes

- **Markers draw beneath the usage lines** instead of above them. A consequence
  of being traces at index 0–1; also the friendlier direction for a complaint
  about distraction.
- **`hoverinfo: 'skip'`** on both traces, so hover matches the shapes' silence
  today. Without it a scatter trace would add hover targets that shapes never had.

## Out of scope

The burn-rate charts have never drawn reset markers and still won't.

## Verification

`scripts/reset_markers.test.mjs` covers `buildResetTraces` against the shipped
source via `extract_js.py`, pinning the trace count and order the persistence
indexes into, the dedup, the null-separated segments, and the `visible` mapping.

The rest is behavior no unit test reaches, checked in a real browser against a
served snapshot of the production DB (never the live 7474 tracker):

- Both entries appear in the legend at every range.
- Clicking either hides only its own markers; the other stays.
- Hidden state survives an auto-refresh, a page reload, and a range switch.
- Double-clicking a reset entry isolates it and it *stays* isolated across the
  next refresh.
- Handlers survive repeated empty-DB renders.
- Markers land on the same timestamps as the shapes they replace.
