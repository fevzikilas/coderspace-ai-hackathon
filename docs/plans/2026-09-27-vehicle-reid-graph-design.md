# Cross-event Vehicle ReID Evidence and Movement Graph Design

## Scope

Add a DAR-scoped evidence layer that compares vehicle crops only across different events. It emits neutral candidate links and a small spatio-temporal evidence graph. It never merges track identities, clusters vehicles globally, infers intent, or changes risk policy.

## Architecture

`detection-svc` owns crop extraction because it already owns image bytes and bounding boxes. A frozen ImageNet-pretrained MobileNetV3-Small feature extractor returns L2-normalized embeddings plus crop quality metadata. An in-memory LRU cache keyed by image id, bounding box, and model version prevents repeat inference.

`gateway` owns session-level comparison because it already orchestrates event results and retains recent runs. A small pure module compares the current event's observations with prior event observations, applies temporal and coarse spatial feasibility, selects minimum-similarity top-K links per source track, and emits public candidate links and graph nodes/edges. Raw embeddings remain internal and are bounded by the existing run retention model.

`risk-agent-svc` receives a compact candidate-link context in the existing assessment request. Prompt and result context explicitly state that a candidate visual linkage is not confirmed identity and cannot independently increase risk. Existing deterministic risk policy and thresholds remain unchanged.

`web-ui` extends the current investigation surface. Leaflet draws candidate nodes and dashed edges. A graph edge selection opens a compact details view with both event/track identifiers, appearance score, time gap, coarse distance, feasibility values, and two crop views reconstructed from the existing image endpoint plus stored bounding boxes.

## Data Flow

1. Detection and georeferencing run as today.
2. Matched track boxes are sent to `detection-svc /appearance/embed`.
3. The gateway builds current observations with event id, track id, timestamp, position, class, crop reference, quality, and normalized embedding.
4. Only prior observations from different events are compared.
5. Pairs with non-positive time order, excessive temporal gap, missing required time, or excessive implied straight-line speed are rejected. Missing coordinates are allowed only when spatial checking is not possible and are marked as such.
6. Feasible pairs above minimum cosine similarity are sorted and capped at top-K per current track.
7. Public links and graph are attached to the run result; internal embeddings are retained only in gateway memory.
8. Candidate summaries are supplied to the risk agent, then the existing pattern and risk flow continues.

## Public Contract

`result.candidate_vehicle_links[]` contains link id, source/target event and track ids, relation `POSSIBLE_SAME_VEHICLE`, appearance similarity, temporal gap, optional spatial distance and implied speed, temporal/spatial feasibility, and evidence crop/model metadata.

`result.vehicle_graph` contains `nodes[]` with event id, track id, timestamp, coarse position, class, crop reference, and quality; and `edges[]` mirroring the candidate evidence. A chain of edges carries no transitive identity meaning.

## Configuration

Gateway environment variables expose minimum similarity, top-K, maximum temporal gap, maximum implied speed, and maximum retained observations. Detection configuration exposes model name, cache size, crop padding, and minimum crop dimensions. Defaults favor a sparse, readable demo graph.

## Failure Behavior

Appearance extraction is evidence enrichment. An unavailable embedding model or unusable crop does not invalidate detection, movement, pattern, or risk processing. The vehicle-link step completes with an empty graph plus a clear diagnostic. Individual low-quality crops are skipped and reported. No silent fallback to a handcrafted descriptor is labeled as pretrained ReID.

## Validation

Focused tests prove same-event exclusion, temporal/spatial rejection, similarity threshold and top-K behavior, graph propagation through dashboard state, risk prompt guardrails, embedding normalization/cache behavior, and UI production compilation. An offline diagnostic may report similarity distribution, pair acceptance counts, candidate counts, and contact sheets, but never identity accuracy without ground truth.
