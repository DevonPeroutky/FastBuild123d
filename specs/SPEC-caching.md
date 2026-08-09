# FastBuild123 Caching Subsystem — Spec (v2)

Supersedes v1 after design review (2026-08-08). The provenance-chain core survives;
the delivery is re-sequenced into phases, caching becomes automatic rather than
annotated, and several v1 bets are explicitly reversed (§7).

## 1. Goal

Make slider-driven parameter iteration feel live by ensuring a parameter change only
pays for the kernel work that parameter actually affects. Mechanism: re-execute the
whole model on every change, but automatically memoize model-file functions with
provenance-chained keys so unchanged calls are dict lookups — including their display
meshes.

**Design stance (changed from v1):**

- **No cache decorator.** `@app.part()` remains purely a UI concept (all-primitive,
  defaulted params → widgets). The harness auto-wraps every top-level function in the
  model file with memoizing logic after `exec` — call-time global lookup finds the
  wrapper, so composition in ordinary Python (`ribbed(hollowed(base(w), wall), n)`)
  is the wiring. `@fastbuild.no_cache` is the opt-out for impure functions.
- **Bypass, never error.** Un-encodable arguments, unsound call graphs, and
  builder-context calls run uncached and are reported via instrumentation. Key
  encoding returns `CacheKey | Error` per repo standards; nothing raises.
- **Staleness analysis is load-bearing, not a guard.** A cache hit short-circuits
  all callees, so a function's key must cover the source of everything it calls.
  When the analysis cannot prove coverage, that function degrades to bypass.

**Stated preconditions / adoption limits:** caching pays off only for models written
as compositions of separate functions — boolean-fused chains (`shell += rib`) have no
cacheable substructure. Builder-mode models (`with BuildPart():`) bypass entirely.

**Non-goals (permanently out of scope):** a dataflow graph engine or UI-level wiring
of shape inputs, incremental computation inside a kernel op, caching arbitrary
undecorated scripts outside the model file, distributed/shared caches.

## 2. Success criteria

Measured against the committed reference model (Phase 0), not hypothetical numbers.

1. **Phase 3** (re-filed from Phase 1 by the v2 review; concretized by the
   re-review — the old "a parameter that only affects the shell" wording was
   vacuous, since no slider touches only the shell and the shell chain alone is
   ~2273ms): dragging `boss_dia` (touches only `mounting_bosses`, 5ms) costs
   ≤ 105ms; dragging `vent_dia` (touches only the grille branch, 666ms) costs
   ≤ 766ms. **The `vent_dia` figure is wrong and is superseded by
   `SPEC-mesh-caching.md` §2.1 (≤ 1600ms).** 766ms was derived as "666ms build +
   100ms" — it budgets the *build* cost of the branch the slider touches and
   omits the *mesh* cost of the child that changed, which is 850ms on its own.
   No mesh cache of any design reaches 766ms; the lever is drag-time
   `angular_tolerance`, filed there. The `boss_dia` figure stands and is met.
   The ceiling for *any* slider is the cost of the most expensive stage
   it touches — `fin_radius` must rebuild `rounded_fins` (1498ms) and no cache
   can help it; that ceiling is stated, not hidden. Phase 1 cannot deliver even
   the `boss_dia` case: its single mesh is keyed on the part function's *call*
   key, so any new parameter combination re-tessellates the whole Compound (~1s
   — ten times that budget on its own, however well the geometry layer hits).
   Drag responsiveness arrives with per-child meshing, not before.
2. **Phase 1:** re-visiting a previously-seen parameter combination costs ≤ 150ms
   end-to-end including render (mesh hit on the part function's *call* key; the
   budget absorbs a ~28ms part-level deepcopy and ~13ms payload serialization —
   the unmeasured term is browser parse + GPU upload of the ~6.7MB payload).
   **The budget stands; the mechanism is superseded by `SPEC-mesh-caching.md`
   §3.5.** The call-key mesh cache is retired — a revisit now rebuilds with all
   geometry hits and all per-instance mesh hits, measured at 47ms, so the
   pre-build short-circuit is no longer worth a second key derivation.
3. Editing any model-file function's body — including helpers, including code inside
   comprehensions — always invalidates dependent cache entries (zero stale-geometry
   cases from *function-body* edits; module-level constants and sibling-module
   imports are a known deferred hole — §6).
4. A cache hit never returns an object whose later mutation — attribute **or CAD
   structure** — corrupts subsequent hits. This is a **store-side** guarantee; its
   mirror (the provenance tag on the handed-out copy surviving in-place mutation)
   is a documented contract limit — see §3 Guards and the §5 mirror test.

## 3. Phases

### Phase 0 — Groundwork ✅ (2026-08-08)

**Reference model:** `examples/finned_enclosure.py` — a finned, vented instrument
enclosure built from seven small functions and assembled as a `Compound` of three
provenance-friendly children (shell / grille / bosses). `scripts/bench_model.py`
times the stages by wrapping the model file's own namespace, which is the same
call-time global-lookup seam Phase 1's wrappers will use. At the defaults
(Apple silicon, build123d 0.11.1):

| stage | ms | |
|---|---:|---|
| `rounded_fins` | 1498 | fillet the fin tips |
| `vent_grille` | 666 | = `perforated_panel` 276 + `chamfered` 390 |
| `hollowed` | 648 | shell the finned extrusion |
| `extruded_body` | 95 | |
| `finned_profile` | 32 | |
| `mounting_bosses` | 5 | |
| **build** | **2950** | |
| **tessellate** | **1021** | |
| **total** | **3971** | vs. 51–448ms for `examples/enclosure.py` |

`bench_model.py --sweep` drives every slider to both ends and exits non-zero on
any failure; the committed defaults pass it.

**What Phase 0 taught us, in rough order of impact on later phases:**

1. **§2.2 needs the mesh checked before the build, not before tessellation.**
   The key `(part_name, source_hash, frozen_param_values)` is knowable before
   `part_def.fn` is called, and it has to be used there — a revisit that still
   pays the 2950ms build cannot land under 150ms.
2. **`ocp_tessellate` memoizes — but not for cache-served copies** (corrected by
   the v2 re-review; the earlier ~424ms skip figure was wrong). Its
   process-global LRU (`tessellator.py:147`, 256MB, keyed on geometry, tunable
   only via `OCP_CACHE_SIZE_MB` — ambient third-party state our design now
   depends on) hits for a freshly *rebuilt* equal shape (~19ms) but **misses
   for a `BRepBuilderAPI_Copy` deepcopy** — exactly the object our store hands
   out on a hit. Measured: cold 1044ms; rebuilt-equal 19ms; deepcopy of a
   previously-tessellated shape 957ms. Cold-pass breakdown: `to_ocpgroup`
   9.9ms, `tessellate_group` 958.8ms, `numpy_to_buffer_json` 4.9ms. So the
   Phase 1 mesh cache is not polish on a warm memo — it is the only thing
   standing between a geometry hit and a full ~957ms re-tessellation.
   Corollary: a *partial* hit (geometry hits, mesh misses) can tessellate
   slower than no geometry cache at all, because deepcopied children miss a
   memo that freshly-built children would hit — Phase 3's per-child meshing
   inherits this constraint.
3. **Mesh JSON is big:** ~4MB for the 160-hole grille, ~8MB chamfered. Worth
   knowing before Phase 3 designs per-node update messages.
4. **The decomposed style is pleasant to write**, with one real friction point:
   each stage needs its own clamp against OCC refusing an extreme (fillet
   radius vs. fin depth, corner radius vs. wall thickness, hole pitch vs.
   diameter). Cross-stage constraints — `corner_radius` vs. `wall` — can only
   be clamped in the composing function, which is a mild argument for keeping
   composition roots thin but not parameter-free. One adjacent hole (v2
   re-review): a function wrapped by a *model-file-defined decorator* hashes as
   `deco.<locals>.wrapper`, so editing the decorated body changes no hash —
   same family as the §6 constants hole, also deferred there.
5. **Keep stage arguments primitive.** Bundling fin geometry into a small frozen
   dataclass read better but would land outside Phase 1's
   `Provenance | Primitive | Composite` union and bypass caching, so the model
   threads floats instead.

**Harness:** `function_source_hashes` now keys by qualname
(`outer.<locals>.helper`, `Shell.method`) via an explicit recursive walk, so
nested same-named functions no longer overwrite each other. (Methods are hashed
for completeness; Phase 1's wiring only ever wraps plain module-level functions.)

### Phase 1 — Auto-memoized geometry (the provenance layer)

**Wrapper wiring** (`harness.py`): `load_model(path) → LoadedModel | Error` stays
pure and **store-free** — its signature does not change; it gains `namespace` and
`parts` fields on `LoadedModel`. A separate boundary function

```python
def install_cache(loaded: LoadedModel, store: Store) -> LoadedModel
```

does the wrapping and returns a new `LoadedModel`: each model-file function in
the namespace is replaced with a memoizing wrapper (membership test:
`isinstance(fn, FunctionType) and fn.__globals__ is namespace` — the
`bench_model.py` seam plus the type guard it actually uses, not "defined at top
level"), and each `PartDef` is rebuilt with its wrapped function
(`dataclasses.replace` — `PartDef` is frozen) into the new `parts` mapping.
**Only `server.py` calls `install_cache`.** The CLI (`cli.py:50`), the harness
tests, and `bench_model.py` keep calling `load_model` unchanged and never see a
store. Session state/migration reads parts from the `LoadedModel`, so the
wrapped defs are what builds actually use.

**Why the PartDef rebuild is required** (v2 review): `registry.py:38` captures
the raw function into `PartDef.fn` at *decoration* time and `builder.py:49`
calls `part_def.fn` directly, so namespace rebinding alone never reaches the
`@part` entry point — whose call key the mesh cache hangs off. Observable today:
`bench_model.py` uses namespace wiring and `enclosure` is absent from its own
stage table. §2.2 is dead without the rebuild. (Ripple: `bench_model.py:131`
must read the namespace from `LoadedModel.namespace` instead of
`part.fn.__globals__` — a wrapper's `__globals__` belongs to the cache module,
and `functools.wraps` cannot fix that.)

Wrappers tag returned shapes with their key via the `_fb_key` attribute —
verified to stick on build123d shapes (no `__slots__`; `b._fb_key = ...`
succeeds). There is **no fallback**: the v2-draft `id(obj) → key` side table is
cut as unnecessary (the attribute works) and unsound (`id()` is reused after GC,
so a freed shape's address landing on an unrelated object fabricates provenance —
wrong geometry served with confidence). Untaggable objects simply flow untagged
and downstream calls bypass.

**Keys** (`fastbuild/cache/keys.py`, pure):
`sha256(qualname, source_closure_hash, encoded_args)`.
Argument encoding is a closed union, matched exhaustively:

```python
Encoded = Provenance | Primitive | Composite
def encode_arg(obj: object) -> Encoded | Error
```

- Provenance: object carries `_fb_key` → use it (the Merkle chain).
- Primitive: int/float/str/bool/None/Enum via `repr()`. Floats as-is; quantization
  is the slider layer's job.
- Composite: tuple/list/dict of the above — **dict keys sorted, containers
  type-tagged** so `[1,2]` and `(1,2)` cannot collide.
- Anything else → `Error` → the call runs uncached, reason recorded.
- build123d value types (`Vector`, `Location`, …): added when the reference model
  needs one, not before.

`CacheKey` is a frozen dataclass with typed fields, hashed at construction.

Arguments are encoded from `inspect.signature(fn).bind(*args, **kwargs)` +
`apply_defaults()` — never from raw `(args, kwargs)` — so positional calls,
keyword calls, and omitted defaults all reach the same key. This is load-bearing
on the reference model: `enclosure` calls `vent_grille` *positionally*
(`finned_enclosure.py:168`) while `build_part` calls it *by keyword and omits
unlisted defaults* (`builder.py:41-46`); both paths must share one entry.

**Call-closure hashing** (`fastbuild/cache/analysis.py`, pure — returns findings as
data; caller logs at the boundary): AST-based, not `co_names` — the harness owns the
parse tree, and AST analysis sees inside comprehensions and lambdas (bytecode
`co_names` misses them) and distinguishes calls from attribute access (avoiding
method-name collisions like a global `offset` vs `build123d.offset`). At load time,
compute each function's transitive closure of called model-file functions; fold
their source hashes into `source_closure_hash`. The walk carries a visited set,
so self- and mutual recursion terminate (each cycle member's hash simply folds
in every member's source; SCC-precise treatment is deferred). Unresolvable calls
(dynamic dispatch, `getattr`, aliasing the analysis can't follow) → function
marked unsound → bypass, surfaced in stats.

**Cacheability is a property of the closure, not the node** (v2 re-review): a
function is cacheable iff it *and every transitive model-file callee* is
cacheable — unsound, impure, and `@no_cache` are all contagious upward over the
same closure the source hashes already traverse. Otherwise a cached caller's hit
would short-circuit exactly the call §1 says must never be short-circuited:
`@no_cache` on a file-reading helper would be silently defeated by any cached
caller, and an unsound callee's unresolved callees would be absent from the
caller's hash → stale hits (the §2.3 failure). Practical consequence, stated
honestly: one `no_cache` near the bottom of a model disables caching for its
entire ancestor chain — the debug trace makes this visible.

**Store** (`fastbuild/cache/store.py`): owned by `ViewerServer.__init__` and
injected explicitly at **two** seams (standards §10 — no process globals):
`install_cache(loaded, store)` — the wrappers close over the store at install
time, baked into the rebuilt `part_def.fn` before any build runs — **and**
`build_part(part_def, values, store)` for the mesh half. Only `server.py`
touches either seam; `load_model` itself stays store-free (see Wrapper wiring),
so the CLI and tests are untouched.
Survives file-save reloads (per-function source hashes invalidate precisely).
**No lock — single-worker execution instead:** builds *and* file-save reloads
run on one dedicated `ThreadPoolExecutor(max_workers=1)` (via
`run_in_executor`, replacing all **three** `asyncio.to_thread` call sites:
`server.py:74` startup load, `:105` build, `:117` reload). The thread race is
real, but the genuinely unsafe shared resource is OCCT, not the dict — a reload
can execute module-level kernel calls while a build sits inside a fillet op, and
a lock around get/put does nothing about that. One worker serializes the kernel.
The precise store invariant is **one mutator thread, snapshot readers** — not
"single-threaded": the worker mutates; the event loop reads `stats() →
FrozenStats` snapshots when filling `BuildOk.cache`. A snapshot taken mid-build
may mix pre/post counters across fields; benign for a footer, and stated rather
than hidden. `ViewerServer.shutdown()` shuts the executor down — tests that
construct a server must not leak the worker thread.
Side benefit: a reload now waits for the in-flight build, closing the
pre-existing stale-render window where an old-source build broadcast *after*
`reload` had replaced state (`server.py:109` vs `123`).
`key → CacheEntry(part, mesh | None)` evicted atomically — one count-based LRU
(default 128), no `mesh:` prefix, no part/mesh eviction skew. `copy.deepcopy` on
insert **and** on hit: build123d's `__copy__` shares the underlying `TopoDS_TShape`
(its shallow copy is a deepcopy with a re-pointed TShape), so only deepcopy
delivers §2.4, and it re-links joints. Copy cost scales with topology, not the
0.5ms cube figure v2 originally quoted: measured on the reference model,
`finned_profile` 0.5ms → `hollowed` 4.6ms → `rounded_fins` 29.1ms →
`mounting_bosses` 28.1ms → the assembled Compound 28.4ms. §2.2's budget absorbs
the ~28ms part-level hit copy explicitly. Verified for Phase 3: Compound
deepcopy preserves child labels, re-parents children to the copy, gives them
distinct wrapped shapes, and carries `_fb_key` through to children (§5 pins
this). The tag rides
along: `Shape.__deepcopy__` copies `__dict__`, so every hit's copy carries
`_fb_key`. That is required — downstream calls read it — but one-sided: deepcopy
protects the *store*, not the *claim*. Nothing invalidates the tag if user code
mutates the copy in place, so a mutated shape passed downstream misattributes
provenance (see Guards for the contract line, §5 for the mirror test; the sound
general fix is content hashing, §6 — not more copying).
**Promotion threshold — gates storage, never tagging.** Three call outcomes, not
two (v2 re-review):

| outcome | key computed | result tagged | store entry |
|---|---|---|---|
| cacheable, ≥ 10ms | yes | yes | yes |
| cacheable, < 10ms | yes | yes | no — just re-runs |
| bypass (any reason) | no — absent or unsound | **no** | no |

Every *cacheable* call tags its result: an untagged result severs the provenance
chain, forcing every downstream consumer to bypass — one sub-threshold stage at
the head of the shell chain would disable the `hollowed` + `rounded_fins` cache
(~72% of the reference build), and `mounting_bosses` (5ms) never earns an entry
but must still be tagged or its Compound child is invisible to Phase 3's
per-child meshing. A *bypassed* call must **never** tag: its key is either
absent (un-encodable args) or a lie (unsound closure), and a fabricated tag
poisons every downstream key that folds it in — the same failure mode that got
the `id()` fallback cut. Both directions are §5 laws.
The threshold's real predicate is **copy cost vs build cost**, not memory:
`mounting_bosses` builds in 5ms and deepcopies in 28ms — caching it would be a
5.6× pessimization. The 10ms default is right for this model's measured copy
costs (0.5–29ms, scaling with face count); tune it against copies, not entry
counts.

**Meshes:** ⚠️ **Retired by `SPEC-mesh-caching.md` §3.5** — the whole of this
paragraph, plus `part_def.fn.call_key`, `Store.get_mesh`/`put_mesh` and the
`mesh` field of `CacheEntry`. Kept for the reasoning, which still explains why
Phase 1 shipped this way. The key here is the part function's **call key** —
`sha256(qualname, source_closure_hash, encoded bound-args)` — computable
*before* `part_def.fn` runs (Phase 0 lesson 1: a revisit that still pays the
build cannot land under 150ms). The wrapper installed by `install_cache`
exposes it: `part_def.fn.call_key(values) → CacheKey | Error`, computed from
signature-bound, defaults-applied values (see Keys). `build_part` checks the
mesh field under that key first and only on miss calls through, populating the
mesh lazily on first display. Mesh JSON is immutable — a mesh hit returns it
directly, no part materialization, no copy (serializing the ~6.7MB reference
payload measures ~13ms server-side).
**Scope honesty (v2 review):** this delivers *revisits only*. Any new parameter
combination is a new call key → mesh miss → the whole Compound re-tessellates
no matter how much of the geometry hit — and cache-served geometry is a
`BRepBuilderAPI_Copy` that *misses* `ocp_tessellate`'s own memo (Phase 0
lesson 2), so this cache is defending ~957ms, not polish. Drag responsiveness
(§2.1) is Phase 3's per-child meshing, not this cache.

**Stats** accumulated from day one: cumulative hits/misses/bypasses, entries,
last-build outcome; logged via the `fastbuild` logger and included in `--debug`
output. This is the data source for Phase 2.

**Debug tracing:** under `view --debug`, every wrapped call logs one line in the
existing transition-log style — qualname, truncated key (first 10 hex chars),
encoded args with provenance arguments shown as truncated upstream keys, outcome
(`HIT`/`MISS`/`BYPASS reason`), and duration — so a build renders as a readable
call tree and provenance chains can be traced key-to-key:

```
── build enclosure ─────────────────────────────────────────
  cache  base(80, 30)              k=3fa2c91e04  HIT      1ms
  cache  hollowed(◆3fa2c91e04, 3)  k=91d04e77b2  MISS  1512ms
  cache  enclosure(80, 30, 3)      k=c00a51f9d3  MISS  1544ms
```

Formatting stays a pure function (statelog pattern); the wrapper logs at the
boundary only when debug is enabled.

**Guards:**

- Builder-context: cached function entered while `BuildPart`/`BuildSketch`/
  `BuildLine` is active → bypass, warn once per key (dedup — a drag storm must not
  flood the log).
- Impure names (`open`, `import_svg`, `import_step`, `environ`, `random`, `time`,
  `input`) in a function's AST → that function is not cached (skip, not just warn).
- `@fastbuild.no_cache` — explicit opt-out; also the documented fix in warnings.
- **In-place mutation of a stage result is outside the caching contract.**
  `_fb_key` is a plain attribute; nothing observes writes to the shape, so
  mutating a returned shape (topology, joints) and passing it downstream caches
  wrong geometry under the pre-mutation key. Documented next to `no_cache` (the
  fix: mutate inside a wrapped function so the result is re-keyed, or opt the
  mutating stage out); mirror test in §5. Benign attribute writes
  (`shell.label = ...`) and Compound assembly are fine — the hazard is CAD
  structure.
- **Failures are never cached.** Note for stats readers: a mid-chain failure leaves
  valid upstream entries, so hit counts can look healthy while the user sees an
  error banner.

### Phase 2 — Hit-rate reporting (cut to the server fill)

Design review of v2 (2026-08-08): the seams are not just present, they're
~90% of the feature — `BuildOk.cache: Mapping | None` exists (`protocol.py:49`),
the `#stats` footer div exists (`index.html:25`), and `app.js` already renders
`msg.cache` as `cache N hits / M misses` (`app.js:276-278`). Phase 2 is
therefore ~5 lines of server work, nothing more:

- **Server:** `_run_build` fills `BuildOk.cache` with
  `{"hits": int, "misses": int, "entries": int, "last": "hit" | "miss" | "bypass",
  "stages": int | None, "rebuilt": int, "hit_rate": float | None}`.
  Counts are cumulative per server lifetime; `last`, `rebuilt` and `hit_rate`
  describe this build. `stages` is the selected part's call-closure size (exposed
  as `part_def.fn.stage_count` by the wrapper — the cold-build lookup count);
  `rebuilt` is this build's misses + bypasses delta;
  `hit_rate = clamp((stages − rebuilt) / stages, 0, 1)` — clamped because a
  helper called from a loop can push `rebuilt` past `stages`. A mesh-hit build is
  `rebuilt = 0 → 100%`. No new protocol message — rides the existing
  `build_status` broadcast, and the existing `last_status` replay on connect
  populates the footer for late-joining clients.
- **Frontend:** one single-line "Cache Hit Rate" progress bar in the `#stats`
  footer (label · rounded track · tabular-nums percentage, ShadCN-Progress
  styling hand-written against the app's existing palette — no
  framework/build-step added). Hidden while `hit_rate` is null; cumulative
  hits/misses and the rebuilt/stages breakdown live in the row's tooltip; the
  timings line renders beneath as before.

Still deferred: per-build color shift on `last`, bypass-reason tooltips. The
convention-teaching job (why a call bypassed) is carried by the Phase 1
debug-trace and stats surface, which reports the same reasons.

### Phase 3 — Per-child meshing → **superseded by `SPEC-mesh-caching.md` ✅ (2026-08-09)**

Still the piece that makes dragging fast rather than only revisits — §2.1 lives
here, not in Phase 1 — but the mechanism below was reversed after checking what
already exists. Keeping the original text would mislead; keeping the reasoning
would not, so the reversal is recorded here and the design lives in its own spec.

**The gate is met.** Phase 1's debug trace on the reference model shows real
decomposition: 8 geometry entries, 5 hits on a `boss_dia` drag, build 3123ms →
60ms. The BREP-bytes-hashing fallback (§6) is not needed.

**What was planned:** parts return Compounds whose provenance-tagged children
tessellate individually against the mesh cache; scene JSON stitched from cached +
fresh nodes; a `ChildMesh` type, `Store.get_child_mesh`/`put_child_mesh`, a
SPEC-ui revision for per-shape provenance ids, and a per-node update message.

**Why it was reversed:** `ocp_tessellate` already memoizes per instance in a
process-global LRU, and `tessellate()` takes `cache_key` as a *parameter*
(`tessellator.py:567`). Supplying our provenance digest instead of its
serialize-and-hash content id gets per-child mesh caching with no new types, no
JSON re-assembly, no store change and no protocol change — roughly 15 lines in
`builder.py`. Phase 0 lesson 2 (a `BRepBuilderAPI_Copy` misses that memo) stops
being a constraint to work around and becomes the bug the substitution fixes.

See `SPEC-mesh-caching.md` for the mechanism, invariants, measured results and
the recommendation to retire this spec's root mesh cache (§3.5 there).

## 4. Module layout

```
fastbuild/                 # existing package
  cache/
    keys.py                # encoding union, CacheKey, provenance tags   (pure)
    analysis.py            # AST call-closure hashing, impurity findings (pure)
    store.py               # CacheEntry LRU, deepcopy discipline, stats (one mutator thread, snapshot readers)
  harness.py               # existing — LoadedModel gains namespace+parts; new install_cache(loaded, store)
  builder.py               # existing — consults mesh cache around tessellation
                           #   (retired; see SPEC-mesh-caching.md §3.5)
  server.py                # existing — owns the store + single-worker executor, fills BuildOk.cache
  static/app.js, app.css   # existing — unchanged (Phase 2 keeps the plain-text footer)
examples/finned_enclosure.py  # Phase 0 reference model
scripts/bench_model.py        # Phase 0 stage/part timing + --sweep extremes check
```

## 5. Test plan

- **Keys as laws** (property tests — this subsystem is mostly laws):
  `encode(x) == encode(y) iff x == y`; dict-ordering stability; tuple/list
  non-collision; kwargs/positional/omitted-defaults equivalence (via
  `Signature.bind` + `apply_defaults`).
- **Tagging laws (both directions):** every *cacheable* call's result carries
  `_fb_key`, including calls under the promotion threshold (the severed-chain
  regression); **no bypassed call's result carries `_fb_key`** (a fabricated tag
  poisons downstream keys). Compound deepcopy carries child tags through with
  re-parented, distinct children (Phase 3 depends on this).
- **Wiring:** after `install_cache`, every `PartDef.fn` *is* the memoizing wrapper
  (the registry-capture regression — the entry point must not escape wrapping);
  plain `load_model` output stays wrapper-free (CLI/tests see no cache).
- **Staleness:** edit body → miss; edit a helper called from a comprehension →
  miss (the case bytecode analysis misses); provenance chain (change upstream arg →
  downstream miss; revert → both hit); unresolvable call → bypass not stale.
- **Poisoning (store side):** mutate a returned part's **CAD structure** (not just
  `label`) → next hit unaffected.
- **Poisoning (tag side, mirror):** mutate a hit's returned copy in place and pass
  it downstream → the stale tag misattributes provenance. Test pins the documented
  contract-limit behavior (xfail-style) — data for the content-hashing fallback (§6).
- **Concurrency:** file-save reload submitted during an in-flight build runs
  strictly after it (single-worker executor) — no OCCT interleaving, and the
  build's broadcast lands before the reload swaps server state.
- **Failures:** raising function → nothing cached; upstream entries intact.
- **Guards:** builder-context bypass fires once per key; impure-name skip;
  `no_cache`; **contagion:** `no_cache`/impure/unsound on a leaf → every
  transitive model-file caller bypasses (the §1 short-circuit law — see §3
  Analysis).
- **Joints:** present on hit; pickle round-trip preserves joints (xfail-allowed —
  data for the disk punt).
- **UI:** `BuildOk.cache` shape validated in protocol tests (including
  `stages`/`rebuilt`/`hit_rate`); `hit_rate` laws — clamping, `stages=None` →
  null, mesh-revisit → 1.0 — unit-tested on `cache_summary`; footer payload
  driven by the existing `FakeClient` message capture.
- **Perf:** tracked benchmark against the reference model with a recorded baseline;
  CI asserts only coarse ratios (hit < miss/10). No hard ms assertions on shared
  runners — a muted flaky perf test is worse than none.

## 6. Punted (carried from v1, with forward-compat notes)

| Punted item | Why | Forward-compat provision |
|---|---|---|
| Disk persistence | Serialization + on-disk eviction + corruption handling is its own project; in-memory covers the slider loop | Store behind an interface; keys content-addressed; joints pickle test written now. A `framework_cache_epoch` key field joins the scheme **with** this layer (in-memory caches die with the process, so the field can't fire before then) |
| Content-hashing foreign shapes (BREP bytes) | Costs ms per lookup; bypass + instrumentation pushes users onto provenance | Bypass reason reserves the space; later `cache="content"` opt-in |
| File dependencies (`files=[...]`, hashing imported SVG/STEP) | Needs watch + hash machinery; impure-name skip flags the risk | Additive kwarg |
| Subprocess build isolation + cancellation | Orthogonal to caching correctness | Keys serialize trivially |
| Byte-accurate memory accounting / draft-LOD / shared disk cache | Polish on layers that don't exist | Promotion threshold + entry cap as manual outs |
| **Module-level constants + sibling-module imports (v2 review, S4)** | A `WALL = 3` edit changes no function's source segment and trips no impure name → silent stale geometry on file-save reload; `from helpers import bracket` is invisible to both the file watcher (`server.py` watches only `model_path`) and the closure analysis. Known staleness hole — deferred, not covered by any Phase 1 mechanism | Planned fix, additive to the key scheme: fold one sha256 of the module's non-function top-level source (the "prelude") into every `source_closure_hash` — one hash per file, zero name resolution, over-invalidates but is sound, and covers `import`-line edits for free. For sibling modules: a resolved callee defined under the model file's directory but outside the model namespace → unsound → bypass. Until built: documented limitation; workaround is `no_cache` on functions reading module globals |

## 7. Reversed and cut from v1

- **`@part` as cache boundary → auto-wrapping.** v1's own example
  (`hollowed(base: Part, wall)`) could not be expressed: `@part` requires
  all-primitive defaulted params (`params.py`), renders unknowns read-only, and
  registers everything in the part selector. Shape inputs are wired by the call
  graph, not the UI — so internal steps need no annotation at all.
- **Loud error on un-cacheable args → silent bypass + instrumentation.** Auto-caching
  cannot raise without breaking existing models; also a raise here would surface as
  a raw traceback via `build_part`'s catch-all. The footer/stats surface takes over
  the convention-teaching job.
- **`copy.copy` → `copy.deepcopy`** (shared-TShape caveat; ~0.04ms premium, real
  guarantee, joint re-linking).
- **Process-wide store → `ViewerServer`-owned.** v2 initially added a
  `threading.Lock` for the `to_thread(build_part)` vs `to_thread(load_model)`
  race; the v2 review replaced it with the single-worker executor (§3 Store) —
  the race is real but the unsafe shared resource is OCCT, not the dict, and one
  worker serializes both.
- **`co_names` analysis → AST call-closure analysis** (comprehension blind spot,
  method-name false positives).
- **Cut (v2 review):** the `id(obj) → key` tagging fallback — attribute tagging
  verified sufficient on build123d shapes, and `id()` reuse after GC fabricates
  provenance for unrelated objects.
- **Considered and rejected: whole-build result cache** —
  `(part, source hash, params) → (Compound, mesh)`, no wrappers, no analysis, no
  tagging; ~10% of Phase 1's machinery, and it delivers §2.2 alone. Rejected
  because the drag-responsiveness value (§2.1) lives in Phase 3's per-child
  meshing, which requires per-stage provenance — Phase 1 is that substrate; a
  whole-build cache is a dead end that would be ripped out when Phase 3 lands.
- **Re-filed, not cut:** simple-globals key folding — v1 cut it as "impure-skip
  covers it"; the v2 review showed it doesn't. Now the prelude-hash follow-up
  in §6.
- **Cut:** `framework_cache_epoch` (until disk), float-identity test (tests the
  dict), "wall time saved" stat (requires counterfactual timing), `version=` as a
  staleness guard (field stays on `PartDef`, dormant — decorator lines are outside
  `ast.get_source_segment`, so bumping it never changed the hash anyway).

## References

- `specs/SPEC-mesh-caching.md` — supersedes Phase 3; corrects §2.1's `vent_dia` target
- [build123d issue #801 — cq_cache port POC and failure analysis](https://github.com/gumyr/build123d/issues/801)
- [cq_cache plugin (CadQuery)](https://github.com/CadQuery/cadquery-plugins/tree/main/plugins/cq_cache)
- [r/build123d — "Caching Build123D objects?" (origin thread for #801)](https://www.reddit.com/r/build123d/comments/1gnvd4n/caching_build123d_objects/)
- Design review, 2026-08-08 (plan-review agent) — findings folded into §7
- Design review of v2, 2026-08-08 (plan-review agent) — folded in: S1 (§2.1
  re-filed to Phase 3), S2 (threshold gates storage, tagging unconditional),
  S3 (`PartDef.fn` rebuilt with the wrapper; `__globals__` membership test),
  S4 (constant/sibling-import staleness hole re-filed as the §6 prelude-hash
  follow-up), S5 (tag-survives-mutation contract limit + mirror test),
  S6 (id-fallback cut),
  S7 (lock → single-worker executor, stale-render fix), S8 (Phase 2 cut to server
  fill, Phase 3 gated on Phase 1 stats), S10 (store injected at both `load_model`
  and `build_part`) — across §2, §3, §4, §5, §7
- Design re-review, 2026-08-08 (plan-review agent, second round) — blockers folded
  in: B1 (cacheability contagious over the closure), B2 (three-state
  tag/store/bypass table; bypasses never tag), B3 (Phase 0 lesson 2 corrected —
  `ocp_tessellate` memo misses on deepcopies, mesh cache defends ~957ms),
  B4/B5/H4 (`install_cache` split; `LoadedModel.namespace`+`parts`; CLI/tests
  untouched), B6 (call key, `fn.call_key(values)` seam), H1 (real deepcopy
  costs; threshold = copy-vs-build), H2 (§2.1 named parameters + stated
  ceiling), H3 (§2.3 qualified), H5 (one-mutator invariant, executor shutdown,
  three call sites), M1–M6, L1–L3
