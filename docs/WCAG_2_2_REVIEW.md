# WCAG 2.2 A/AA Review

Review date: 2026-07-11
Target: Foglight Standard mode, Overview, incident drawer, Watch Center,
Settings, provider attribution, map, loading/error/offline states, and 200%
zoom/reflow.
Standard: [WCAG 2.2](https://www.w3.org/TR/WCAG22/), levels A and AA.

## Result

No known WCAG 2.2 A/AA blocker remains. Automated axe scans reject every
`serious` or `critical` finding in Standard mode, Settings, Overview, the
incident drawer, and Watch Center. Keyboard, focus restoration/containment,
24 CSS-pixel targets, responsive layouts, reduced motion, and 200% zoom/reflow
are asserted in the Playwright suite.

The strengthened Standard scan initially found real defects: low-contrast
secondary text, an active button below 4.5:1, an unnamed ISS map marker, and an
untitled TV iframe. Those defects were fixed rather than waived. The
optional-panel CTA was also moved from a fixed viewport overlay into the grid;
a geometric browser assertion proves it does not intersect map attribution.

## A/AA checklist

| Area | Evidence and disposition |
|---|---|
| 1.1.1 Text alternatives | Interactive map markers and the TV frame have accessible names; axe passes. |
| 1.3.1–1.3.5 Structure and purpose | Landmarks, headings, native buttons/inputs, labels, table-free lists, and input purposes are programmatic; ARIA snapshots cover primary surfaces. |
| 1.4.1 Use of color | Freshness, category, status, selection, and change use text/shape in addition to color. |
| 1.4.3 Contrast | Full axe color-contrast rules pass in Standard, Settings, and Overview. Secondary text is `#77839d`; active theater controls use `#306bb8` with white text. |
| 1.4.10 Reflow | 520 px layouts and 200% zoom have no horizontal document/dialog overflow in covered workflows. |
| 1.4.11 Non-text contrast | Controls retain visible borders/focus and state text; axe has no serious/critical finding. |
| 1.4.12 Text spacing | Layouts use flexible containers and scrolling panes; no clipping appears in responsive visual baselines. |
| 1.4.13 Hover/focus content | Leaflet tooltips/popups and drawers remain dismissible; core facts are also available in lists/drawers. |
| 2.1 Keyboard | Filters, cards, settings, dialogs, lists, and Leaflet map keyboard controls are operable without pointer input. |
| 2.2 Timing | Wall rotation and stream motion pause on interaction; reduced-motion mode disables automatic wall cycling. No destructive session timeout exists. |
| 2.3 Seizures/animation | No flashing content; reduced-motion tests suppress skeleton/pulse/automatic movement. |
| 2.4 Navigation/focus | Headings and accessible names identify regions. Drawer and Settings focus is contained and restored; Escape closes overlays. |
| 2.5.1/2.5.7 Pointer gestures/dragging | Map pan/zoom has keyboard controls and every incident remains available through non-map lists/search/catalog. |
| 2.5.3 Label in name | Visible button labels and accessible names agree. |
| 2.5.8 Target size | Automated checks enforce at least 24×24 CSS pixels for active Overview, drawer, and Watch Center controls; inline-link exemptions remain inline. |
| 3.1 Language | The document declares `lang="en"`; source and UTC terminology is expanded or contextualized. |
| 3.2 Predictability | Navigation, filters, display modes, Settings, and close controls stay consistent and do not change context on focus. |
| 3.3 Input assistance | Watch/settings validation preserves entered state where possible and reports failure without silently committing invalid data. Authentication criteria are not applicable: Foglight has no account/login. |
| 4.1 Name, role, value | Native controls plus bounded ARIA are checked by axe and ARIA snapshots; no serious/critical violation remains. |

## Commands

```powershell
npx playwright test --grep "baseline shell|passes accessibility" --reporter=line
npm run test:browser
```

Automated tooling cannot prove subjective clarity or every assistive-technology
combination. The release checklist therefore retains keyboard, 200% zoom,
visible focus, attribution visibility, and screen-reader-name spot checks for
the packaged executable.
