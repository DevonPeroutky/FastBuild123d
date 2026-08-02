# FastBuild123 Caching Subsystem — Spec

## 1. Goal

Make slider-driven parameter iteration feel live by ensuring a parameter change only pays for the kernel work that parameter actually affects. Mechanism: re-execute the whole model on every change, but memoize decorated part functions with provenance-chained keys so unchanged calls are dict lookups — including their display meshes.

**Non-goals (permanently out of scope):** a dataflow graph engine, incremental computation *inside* a kernel op, caching arbitrary undecorated scripts, distributed/shared caches.

## 2. Success criteria for MVP

1. On the reference model (filleted base ~2s + shell ~1.5s + fast bosses), dragging a parameter that only affects the shell costs ≤ shell-time + 100ms overhead; re-visiting a previously-seen value costs ≤ 150ms end-to-end **including render** (mesh cache hit).
2. Editing a decorated function's body always invalidates its cache (zero stale-geometry cases from own-body edits).
3. A cache hit never returns an object whose later mutation corrupts subsequent hits.

## 3. Core design (MVP)

### 3.1 Decorator and execution harness

```python
@app.part()                      # also: @app.part(version=1, cache=True)
def filleted_base(width: float, depth: float, height: float, r: float) -> Part: ...
```

- The harness (not `import`) loads the user's model file: read source → `ast.parse` → `compile` → `exec` in a managed namespace. The harness therefore always holds the exact source text, sidestepping the `inspect.getsource` failure that blocked the cq_cache port inside CQ-editor (build123d issue #801).
- The decorator registers the function and wraps it with the memoizing logic. The wrapped function remains an ordinary callable returning ordinary build123d objects.
- Re-execution on parameter change or file save (via `watchfiles`) runs in-process for MVP.

### 3.2 Cache key (provenance scheme)

```
key = sha256(
    fn_qualname,
    source_hash,          # sha256 of ast.get_source_segment for this function
    version,              # from @part(version=N), default 0
    encoded_args,         # per-argument encoding, see below
    framework_cache_epoch # bump to invalidate everything on framework upgrade
)
```

Argument encoding, in order of attempt:

1. **Provenance tag**: object carries `_fb_key` (it came out of a cached call) → use that key. This is the Merkle chain.
2. **Primitive**: int/float/str/bool/None/Enum → `repr()`. Floats are used as-is; quantization is the slider layer's job (steps defined in `Range`).
3. **Composite of the above**: tuple/list/dict, recursively encoded.
4. **build123d value types**: `Vector`, `Location`, `Axis`, `Plane`, `Color` → encoded from their defining floats.
5. **Anything else** (including un-tagged shapes): **raise a clear error** at call time: *"argument `base` of `hollowed` is not cacheable: pass shapes produced by @part functions, or mark cache=False."* MVP fails loudly rather than hashing geometry bytes.

Return values get tagged: `result._fb_key = key` (on the build123d wrapper object; verify attribute assignment sticks — fallback is an identity-keyed side table `id(obj) → key` scoped to one execution pass).

### 3.3 Store

- **In-memory only.** One process-wide `CacheManager` holding `key → (part, mesh)`.
- LRU eviction by entry count (default 128 entries; configurable). Count, not bytes, for MVP — measuring BREP memory footprint is unreliable and not worth it yet.
- **Hits return `copy.copy()` (shallow copy) of the stored part**, never the stored object (mutation-poisoning defense). Store also keeps its own private copy at insert time for the same reason.
- Instrumentation from day one: per-call hit/miss, wall time saved, current entry count — surfaced to the UI later, logged for now.

### 3.4 Mesh cache

- Tessellation results (via `ocp_tessellate`, same as ocp-vscode uses) are cached under the same key with a `mesh:` prefix, populated lazily on first display.
- Viewer requests mesh by provenance key → hit means zero kernel *and* zero tessellation work.

### 3.5 Staleness guards (MVP subset)

- **Own body edits**: covered exactly by `source_hash`.
- **One-level helper hashing**: for each name in `fn.__code__.co_names` that resolves (in the managed namespace) to a plain function *defined in a harness-loaded file*, fold that helper's source segment hash into the key. Catches the "edited an undecorated helper" case — the most common real staleness bug. One level only; no recursion in MVP.
- **Simple globals**: `co_names` entries resolving to primitives/composites-of-primitives → fold values into the key.
- **Warnings, not analysis, for the rest**: at decoration time, warn (once, with the name) when `co_names` contains (a) a global resolving to a complex/mutable object, (b) known-impure names: `open`, `import_svg`, `import_step`, `environ`, `random`, `time`, `input`. Warning text tells the author the fix ("pass it as a parameter" / "add version= and bump manually").
- **Builder-context guard**: if a cached function is entered while a `BuildPart`/`BuildSketch`/`BuildLine` context is active on build123d's context stack, warn and **bypass the cache** for that call (execute directly). Cache boundaries must be algebra-style pure functions; this makes the rule self-enforcing instead of documentation-only.
- **Manual escape hatch**: `@part(version=N)` — bump to force invalidation for anything the system can't see.

## 4. Punted (explicitly, with forward-compatibility notes)

| Punted item | Why | Forward-compat provision |
|---|---|---|
| **Disk persistence** (pickle-first, joints round-trip test, BREP fallback) | Serialization + eviction-on-disk + corruption handling is a project of its own; in-memory covers the slider loop, which is the MVP's whole point | `CacheManager` store is behind an interface; key scheme already content-addressed, so a disk layer slots in without key changes. Joints-survive-pickle test written *now* (xfail-allowed) so the format question has data when we get there |
| **Recursive call-graph hashing** | Sound version is program analysis on dynamic Python; unsound versions cause spurious invalidation (the bytecode-hashing failure from issue #801) | One-level hashing + `version=` covers the practical gap; keys already include a slot for deeper source hashes |
| **Content-hashing shape arguments** (BREP-bytes hashing for foreign shapes) | Costs ms per lookup; MVP's loud error pushes users onto provenance, which is both faster and the convention we want | Error message reserves the design space; can later add `cache="content"` opt-in per function |
| **File dependencies** (`@part(files=[...])`, hashing imported SVG/STEP bytes) | Needs watching + hashing machinery; impure-name warning flags the risk meanwhile | Decorator already accepts kwargs; adding `files=` is additive |
| **Subprocess build isolation + cancellation** (hung-fillet protection) | Process orchestration is orthogonal to caching correctness; in-process is fine for models that already work | Nothing in the cache assumes same-process; provenance keys serialize trivially |
| **Byte-accurate memory accounting / draft-LOD mode / cross-project shared disk cache / package-version keys** | Each is polish on a layer that doesn't exist yet | Epoch field + version field give manual outs until then |

Two punts are *deliberate product bets, revisit if wrong*: (a) erroring on un-tagged shape arguments may prove too strict if users mix in shapes from raw build123d code — the fallback is BREP-bytes hashing; (b) count-based LRU may evict too eagerly on assembly-heavy models — the fallback is size estimation via serialized length.

## 5. Module layout

```
fastbuild123/
  cache/
    keys.py        # encoding, provenance tags, source-segment hashing
    store.py       # CacheManager, LRU, shallow-copy discipline, stats
    analysis.py    # co_names resolution, helper hashing, impurity warnings
  harness.py       # file load/exec, AST index (qualname → source segment)
  decorator.py     # @part: registration + cache wiring
```

## 6. Test plan

- **Correctness**: edit-body → miss; edit one-level helper → miss; same args → hit; provenance chain (change upstream arg → downstream miss; revert → both hit); float identity (`2.0` vs `2.0` hit; `2.0` vs `2.0000001` miss — documenting that quantization is the caller's job); kwargs/positional equivalence.
- **Poisoning**: mutate a returned part → next hit unaffected.
- **Guards**: builder-context bypass fires; impure-name warning fires; complex-global warning fires; un-tagged shape raises the specified error.
- **Joints**: joints present on cache hit (in-memory); pickle round-trip preserves joints (xfail-allowed — data collection for the disk punt).
- **Perf harness**: scripted reference model asserting the §2 numbers, run in CI with generous margins.

## 7. Open decisions to sanity-check

1. **MVP is in-memory only** — the 11s→0.03s wow-factor of cq_cache came from disk persistence across runs, but the product's core loop is intra-session slider dragging, where disk adds risk without UX gain.
2. **Un-cacheable shape arguments raise instead of degrade** — strictness now shapes user habits toward the provenance convention everything else depends on.

Both have documented fallbacks in §4 if real usage disagrees.

## References

- [build123d issue #801 — cq_cache port POC and failure analysis](https://github.com/gumyr/build123d/issues/801)
- [cq_cache plugin (CadQuery)](https://github.com/CadQuery/cadquery-plugins/tree/main/plugins/cq_cache)
- [r/build123d — "Caching Build123D objects?" (origin thread for #801)](https://www.reddit.com/r/build123d/comments/1gnvd4n/caching_build123d_objects/)
