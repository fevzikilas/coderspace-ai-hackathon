# Vehicle ReID Evidence Graph Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a sparse, epistemically neutral cross-event vehicle appearance evidence graph from crop embedding through gateway, risk context, and investigator UI.

**Architecture:** Detection service produces cached frozen MobileNetV3-Small crop embeddings. Gateway performs pure cross-event cosine matching with temporal/spatial guards and bounded top-K selection, then publishes candidate links and a graph. Risk receives read-only candidate context; the current Leaflet investigation UI renders selectable evidence without changing identity or risk policy.

**Tech Stack:** Python 3.12, FastAPI, Pillow, PyTorch/torchvision, Pydantic, pytest, React 18, TypeScript, React-Leaflet, Vite.

---

### Task 1: Frozen crop appearance representation

**Files:**
- Create: `detection-svc/app/appearance.py`
- Modify: `detection-svc/app/config.py`
- Modify: `detection-svc/app/schemas.py`
- Modify: `detection-svc/app/main.py`
- Modify: `detection-svc/requirements-model.txt`
- Modify: `docker/detection-svc.Dockerfile`
- Test: `detection-svc/tests/test_appearance.py`

1. Write tests for L2 normalization, crop clipping/quality, deterministic LRU reuse, and endpoint response shape using an injected fake backbone.
2. Run `PYTHONPATH=detection-svc pytest detection-svc/tests/test_appearance.py -q`; verify missing module/endpoint failures.
3. Implement `AppearanceExtractor` with MobileNetV3-Small DEFAULT weights, frozen inference, crop padding, quality metadata, and bounded cache.
4. Add the `/appearance/embed` request/response models and endpoint; load the extractor lazily so detection remains available if embedding initialization fails.
5. Run focused and complete detection tests.
6. Commit the task.

### Task 2: Cross-event candidate selection and graph

**Files:**
- Create: `gateway/app/vehicle_reid.py`
- Modify: `gateway/app/config.py`
- Modify: `gateway/app/state.py`
- Test: `gateway/tests/test_vehicle_reid.py`

1. Write pure tests for same-event exclusion, forward temporal ordering, maximum gap, impossible implied speed, missing-coordinate handling, minimum similarity, top-K, stable link ids, and no transitive clustering.
2. Run the focused test and verify expected import/behavior failures.
3. Implement bounded observation storage, cosine similarity, haversine distance, feasibility evidence, top-K selection, and graph serialization.
4. Run focused and complete gateway tests.
5. Commit the task.

### Task 3: Gateway pipeline integration

**Files:**
- Modify: `gateway/app/pipeline.py`
- Modify: `gateway/app/dashboard.py`
- Modify: `gateway/tests/test_gateway.py`

1. Extend gateway tests so mocked appearance observations flow into `candidate_vehicle_links` and `vehicle_graph`, including an appearance failure that leaves the core pipeline successful.
2. Run the focused gateway tests and verify contract failures.
3. Add the additive `vehicle_links` step, call the detection appearance endpoint for tracked boxes, match/register observations, and include public graph fields in results/dashboard state.
4. Keep empty/no-track and drone flows backward compatible.
5. Run complete gateway tests and commit.

### Task 4: Risk evidence context guard

**Files:**
- Modify: `risk-agent-svc/app/main.py`
- Modify: `risk-agent-svc/app/agent.py`
- Modify: `risk-agent-svc/app/facts.py`
- Modify: `risk-agent-svc/app/prompts.py`
- Modify: `risk-agent-svc/tests/test_agent.py`

1. Write tests that candidate summaries appear in the user message and that system rules explicitly prohibit confirmed identity and risk escalation from linkage alone.
2. Run the focused tests and verify failures.
3. Add bounded candidate context to request, Facts, prompt payload, and assessment output without touching policy/rules.
4. Run complete risk tests and commit.

### Task 5: Investigator movement graph UI

**Files:**
- Create: `web-ui/src/components/VehicleLinkPanel.tsx`
- Create: `web-ui/src/components/CropThumbnail.tsx`
- Modify: `web-ui/src/types.ts`
- Modify: `web-ui/src/App.tsx`
- Modify: `web-ui/src/components/MapPanel.tsx`
- Modify: `web-ui/src/components/DetailsModal.tsx`
- Modify: `web-ui/src/components/EventSummary.tsx`
- Modify: `web-ui/src/styles.css`

1. Define additive graph/link types and implement the details panel with neutral copy, keyboard buttons, two crop views, numeric evidence, and empty state.
2. Add dashed candidate edge/node overlays to Leaflet; edge click selects the link and opens the matches tab.
3. Preserve existing colors/layout, use text plus color for states, and add reduced-motion/focus behavior.
4. Run `npm run build`; fix TypeScript or bundling failures.
5. Commit the task.

### Task 6: Configuration, documentation, and diagnostic

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`
- Create: `scripts/vehicle_reid_diagnostic.py`
- Test: `scripts/test_vehicle_reid_diagnostic.py`

1. Write a test for diagnostic aggregation output without asserting identity accuracy.
2. Document model, thresholds, schema, epistemic limits, demo flow, and offline diagnostic usage.
3. Expose explicit matching and embedding settings in environment/Compose.
4. Run the diagnostic on organizer data if embeddings can be produced locally; save only useful non-ground-truth metrics/contact sheet outputs outside source unless intentionally documented.
5. Run focused tests and commit.

### Task 7: Final verification and review

**Files:**
- Review all files changed from `origin/main`.

1. Run all relevant Python service suites and the UI production build.
2. Run the focused validation checklist against test names and inspect the final public JSON shape.
3. Run the offline diagnostic if feasible and report assumptions honestly.
4. Review `git diff --check`, `git status`, and diff scope.
5. Request code review, address Critical/Important findings, rerun verification, and create the final commit.
6. Keep `feature/vehicle-reid-graph` unmerged and report its final commit hash.
