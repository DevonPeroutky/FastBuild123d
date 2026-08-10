# FastBuild123 Mesh Caching — Spec ✅ implemented (2026-08-09)

Shipped as specified. `boss_dia` drags at 84–85ms from the first drag (was
1044ms), revisits at 46–47ms, `vent_dia` unchanged at ~1590ms — all matching §2.
65 tests pass; four are new (§5 laws 1, 2, 3, 5 and the re-mesh guard).

Replaces `SPEC-caching.md` §3 Phase 3 ("Per-child mesh assembly"), which planned a
per-child tessellation loop, JSON re-assembly, a store schema change, and a
per-node protocol message. None of that is needed: `ocp_tessellate` already
memoizes per instance and takes the cache key as a **parameter**. We supply
provenance digests instead of letting it hash geometry. §7 records the reversal.

Numbers are measured against `fastbuild/examples/finned_enclosure.py` on an M-series
12-core machine, consistent with the Phase 0 baseline.

## 1. Goal

Make a slider pay only for the meshes its parameter actually changes.

Phase 1 made geometry cheap and left tessellation whole-part. Dragging `boss_dia`
— which touches one 5ms stage — costs 1044ms, of which 986ms is re-meshing two
children whose geometry came straight out of the cache unchanged. The geometry
layer is doing its job (3123ms → 60ms); the mesh layer erases the win.

## 2. Success criteria

Measured, not projected — from a working prototype of §3.3 run against the
reference model. The prototype also confirmed laws 5.2 (a bare `Part` yields no
overrides), 5.5 (all ids namespaced) and the I3 equivalence check before any
production code was written.

| drag | today | this spec | note |
| --- | --- | --- | --- |
| `boss_dia` (5ms stage) | 1044ms first, 84ms after | **81–101ms from the first drag** | SPEC-caching §2.1 target ≤105ms — passes, thin margin |
| `vent_dia` (grille branch) | 1566ms | **1566ms — unchanged** | see §2.1 |
| `fin_radius` (shell branch) | ~1650ms | ~1650ms | stated ceiling, no cache helps |
| revisit a seen combination | 0.1ms | 47ms | §3.5 — root mesh cache retired; SPEC-caching §2.2 budget is 150ms |

The `boss_dia` range is n=8 across two prototype runs (81.7, 89.6, 82.1, 81.0,
80.7 / 91.8, 101.3, 94.7ms — the second run uniformly slower, cause not
investigated). It clears ≤105ms but not comfortably, and the composition is
worth knowing before treating the target as banked: ~30ms of `deepcopy` on five
geometry hits, ~5ms rebuilding `mounting_bosses`, ~25ms of Compound assembly, and
a ~28ms tessellation floor (`to_ocpgroup` conversion plus `numpy_to_buffer_json`
encoding, both paid on every build regardless of mesh hits). If the target
tightens, that floor — not meshing — is what to attack next.

The headline: **the first-drag penalty disappears.** Today every cached child is
re-meshed once at full price because the store hands out a `BRepBuilderAPI_Copy`
whose serialization — and therefore `ocp_tessellate`'s content hash — differs from
the original's. Keying on provenance removes that failure mode entirely.

### 2.2 Scope honesty: this makes cheap sliders cheap, and that is 1 of 15

On the reference model, exactly one slider — `boss_dia` — touches only a cheap
stage. Nine (`width`, `depth`, `height`, `corner_radius`, `wall`, `fin_count`,
`fin_width`, `fin_depth`, `fin_radius`) reach the shell chain and cost ~4s;
five (`panel_thickness`, `vent_cols`, `vent_rows`, `vent_dia`, `deburr`) reach
the grille and cost ~1.6s. This spec takes that one slider from 1044ms to ~90ms
and leaves the other fourteen where they are, because their work is real.

That is the correct outcome for a *cache* — it cannot skip work the parameter
genuinely changes — but it means per-child meshing alone does not deliver
"dragging feels live" on this model. The remaining levers are mesh quality during
drag (§2.1, 5.4×) and how the model file decomposes. Worth stating before this
phase is read as the finish line.

### 2.1 SPEC-caching §2.1's `vent_dia` target is unachievable — restated

SPEC-caching §2.1 budgets `vent_dia` at ≤766ms, derived as "666ms build + 100ms".
That counts the *build* cost of the branch the slider touches and omits the *mesh*
cost of the child that changed. The grille's mesh alone is 850ms, on geometry that
genuinely changed, so no mesh cache of any design can reach 766ms.

Restated target: **`vent_dia` ≤ 1600ms with per-child meshing.** Closing the rest
is a separate change with a measured lever — mesh cost is driven by angular, not
linear, deflection (the grille is 160 chamfered bores):

```
deviation=0.1  angular_tolerance=0.2 (defaults)   911ms   276,552 verts
deviation=2.0  angular_tolerance=0.2               839ms   276,552 verts
deviation=0.1  angular_tolerance=1.0               170ms    64,392 verts
deviation=2.0  angular_tolerance=1.0               144ms    52,872 verts
```

A coarse `angular_tolerance` during drag with a full-quality re-tessellation on
slider release puts `vent_dia` at ~885ms. Out of scope here; filed in §8.

## 3. Mechanism

### 3.1 What already exists (checked before designing)

| candidate | verdict |
| --- | --- |
| `ocp_tessellate.tessellator.cache` — 256MB `cachetools.LRUCache`, `@cached(cache, key=make_key)` | **Use it.** `tessellate()` takes `cache_key` as a parameter; `make_key` keys on it directly. |
| `BRepBuilderAPI_Copy(shape, copyGeom, copyMesh=True)` | No help. Measured: identical hash to `copyMesh=False`, both differ from the original. |
| OCCT triangulation stored on the `TShape` (incremental meshing skips meshed faces) | Already exploited implicitly; defeated by the store's copy, which is what this spec fixes. |
| `BRepMesh_IncrementalMesh(..., isInParallel=True)` | Already parallel inside OCCT. Nothing to add. |
| Threading the Python build/tessellate loop | Dead end — OCP holds the GIL. Measured 1.01× across 3 threads. |

So the mesh cache we need is already written and already in the process. It is
keyed wrong for our purposes, and it accepts the right key as an argument.

### 3.2 Invariants

The design rests on four, and carries no error handling beyond them.

- **I1 — Tagging.** Every cacheable call tags its result with its key; a bypassed
  call never tags. (Existing SPEC-caching §5 law; unchanged.)
- **I2 — Key soundness.** Equal `CacheKey` ⇒ same qualname, same source closure
  hash, same encoded arguments ⇒ geometrically identical result. Follows from key
  construction plus the purity analysis in `cache/analysis.py`.
- **I3 — Instance locality.** An instance's mesh depends only on its
  location-normalized geometry. `get_instance` strips location into the tree node,
  and per-instance quality comes from the instance's own bounding box
  (`convert.py:1892`), never the assembly's. Verified: a child tessellated alone
  produces byte-identical `vertices`/`normals`/`triangles`/`edges`, `loc` and `bb`
  to the same child tessellated in the group.
- **I4 — Flat image.** The substitution applies only when the `OcpGroup` is a flat,
  same-length, name-matching image of the root's children. Any other structure
  substitutes nothing and behaves exactly as today.

**I2 + I3 ⇒ substituting the provenance digest for the content hash is sound.**
**I4 ⇒ a wrong mesh is unrepresentable**: either the correspondence is exact, or
there is no correspondence and nothing is overridden. There is no partial case, so
there is nothing to validate at runtime and no fallback to get wrong.

I4 earns its keep. An earlier prototype matched instances to children by `TShape`
identity, which silently mapped 1 of 3 children — `ocp_tessellate` unwraps
single-solid compounds during conversion, so a `Part`'s `TShape` is not the
instance's. Positional-plus-name agreement over `group.objects` is exact, and
declining anything else is what makes the mapping safe rather than merely usually
right.

### 3.3 The change

All of it, in `builder.py`:

```python
FB_CACHE_PREFIX = "fb:"


def _provenance_ids(root: Any, group: OcpGroup) -> tuple[tuple[int, str], ...]:
    """(instance ref, cache id) pairs when `group` is a flat, name-matching image
    of `root.children` — otherwise (), leaving ocp_tessellate's content hashing
    in place (I4)."""
    children = tuple(root.children) if isinstance(root, Compound) and root.children else ()
    objects = group.objects
    if not children or len(objects) != len(children):
        return ()
    ids = []
    for obj, child in zip(objects, children):
        if not isinstance(obj, OcpObject) or obj.ref is None or obj.name != child.label:
            return ()
        key = getattr(child, TAG_ATTR, None)
        if isinstance(key, CacheKey):
            ids.append((obj.ref, FB_CACHE_PREFIX + key.digest))
    return tuple(ids)


def tessellate_to_json(obj: Any, name: str) -> Mapping[str, Any]:
    group, instances = to_ocpgroup(obj, names=[name])
    for ref, cache_id in _provenance_ids(obj, group):
        instances[ref]["cache_id"] = cache_id
    instance_meshes, shapes, _id_map = tessellate_group(group, instances)
    return numpy_to_buffer_json({"instances": instance_meshes, "shapes": shapes})
```

`_provenance_ids` is pure. The mutation is of a list `to_ocpgroup` just
constructed and handed us, never a caller's data.

The `fb:` prefix namespaces our digests against `ocp_tessellate`'s bare hex ids so
the two key spaces cannot collide in the shared LRU.

### 3.4 API surface — the cache is wired in exactly once

This spec adds no public API and removes one parameter. The complete surface:

| when | call | effect |
| --- | --- | --- |
| once per model load | `install_cache(loaded, store)` | wraps the model's functions; **the only place a `Store` is injected** |
| once per build | `build_part(part_def, values)` | builds and tessellates; no cache argument |

`build_part` loses its `store` parameter (§3.5). Nothing is lost by that: the
geometry cache was never reached through it — the memoizing wrappers close over
the store at install time — and the mesh cache it did reach is being retired.

**Caching is on if and only if `install_cache` was called.** That is now literally
true rather than approximately: `cli.py` (export) calls `load_model` and invokes
`part.fn(...)` directly, and `scripts/bench_model.py` calls `build_part` with no
store, so both already run uncached by construction. The `store is not None`
gating inside `build_part` was expressing that same condition a second time, in a
second place.

One key type (`CacheKey`), one tagging discipline (I1), one injection point, one
build call.

### 3.5 Retire the root mesh cache

**Decided.** Phase 1's root mesh cache is deleted, not kept alongside the new one.
It is a second key derivation over a second storage mechanism, and per-instance
provenance meshing makes it redundant.

Measured revisit cost with it removed — full build with all geometry hits, full
tessellation with all mesh hits:

```
revisit boss_dia=9.5    build 28.2 + tess 19.3 = 47.5 ms
revisit boss_dia=10.0   build 28.4 + tess 17.9 = 46.4 ms
revisit boss_dia=9.5    build 26.9 + tess 17.9 = 44.8 ms
```

47ms against SPEC-caching §2.2's 150ms budget — a 3× margin. The explicit trade:
revisits go from 0.1ms to 47ms. Accepted; §2.2's budget is the requirement.

**What goes:**

| deleted | file |
| --- | --- |
| `_mesh_key` | `builder.py:42` |
| `build_part`'s `store` parameter and both mesh branches | `builder.py:54,64-66,85` |
| `Store.get_mesh`, `Store.put_mesh`, `_mesh_hits` | `cache/store.py:66-77` |
| `FrozenStats.mesh_hits` | `cache/store.py:25` |
| `_Entry` — with `mesh` gone it holds only `part`, so the LRU maps `CacheKey → part` directly | `cache/store.py:30` |
| `wrapper.call_key`, `bypassing.call_key`, the inner `call_key` | `cache/wrap.py:103,156-165,167` |
| `keys.call_key` — already dead, nothing imports it | `cache/keys.py:138` |
| `store=self.store` at the `build_part` call | `server.py:151` |

`_Entry` disappearing is the one worth noting: it existed only to pair a part with
its mesh. With meshes gone from `Store`, the dataclass is a single-field wrapper
and the `OrderedDict` holds parts directly. That also removes the nullable
`mesh` field, so no illegal `_Entry` state remains to reason about.

One care point when collapsing it: `get_part` currently distinguishes hit from
miss by whether the *entry* is `None`, which survives a stored value of `None`.
Holding parts directly makes `self._entries.get(key) is None` ambiguous — a model
function that returns `None` and runs over the 10ms threshold would be stored and
then read back as a miss. Test membership with `key in self._entries` and fetch
separately. Cheap to get right, silent if got wrong.

**What stays:** `stage_count` (`wrap.py:104,168`) is unrelated — it feeds the
Phase 2 hit-rate footer via `server.cache_summary`, not the mesh cache.

Result: provenance keys identify values, geometry lives in `Store`, meshes live in
the `ocp_tessellate` LRU under `fb:<digest>`, and nothing is keyed two ways.

## 4. Module layout

```
fastbuild/builder.py      # + _provenance_ids (pure); override in tessellate_to_json
                          # - _mesh_key; - build_part's store param and mesh branches
fastbuild/cache/store.py  # - get_mesh, put_mesh, _mesh_hits, FrozenStats.mesh_hits
                          # - _Entry (LRU now maps CacheKey -> part)
fastbuild/cache/wrap.py   # - call_key (both wrappers)
fastbuild/cache/keys.py   # - call_key (already dead)
fastbuild/server.py       # - store= at the build_part call
```

No new modules, no new types, no protocol change. **The diff is net-negative:**
~15 lines added to `builder.py`, more than that removed across five files.

## 5. Test plan

Laws first, per PYTHON_CODE_STANDARDS §9.

1. **Substitution soundness.** With a child served from the geometry cache, the
   rendered mesh is equivalent to the mesh from freshly-built geometry. Compare
   `vertices`, `normals`, `edges` for equality — **not `triangles`**. Measured: a
   shape and its `BRepBuilderAPI_Copy` tessellate to identical vertices, normals
   and edges but a differently-ordered `triangles` buffer. Provenance keying is
   first-writer-wins (sound under I2), so byte-equality against the current path
   is the wrong assertion.
2. **Degradation (I4).** A root that is not a flat labeled `Compound` — a bare
   `Part` such as `vent_grille`, a nested compound, a mismatched label — produces
   byte-identical output to the pre-change path, and `_provenance_ids` returns `()`.
3. **Untagged children.** A child from a bypassed function carries no tag and gets
   no override; its instance keeps the content hash.
4. **Invalidation.** Editing a child's function body changes its closure hash,
   hence its digest, hence its mesh key. No stale mesh survives a source edit.
5. **Namespacing.** Every id fastbuild writes starts with `fb:`.
6. **Pinning.** A golden test over `to_ocpgroup`'s output shape, so an
   `ocp_tessellate` upgrade that moves `cache_id`, `OcpObject.ref` or
   `OcpObject.name` fails loudly (§6).

7. **Revisit budget (§3.5).** Re-selecting a previously-seen parameter combination
   completes within SPEC-caching §2.2's 150ms. This replaces the deleted
   `mesh_hits == 1` assertion, and is the test that guards the trade §3.5 makes:
   it must measure the real path, not a cache counter.

`_provenance_ids` is pure and testable with hand-built `OcpGroup` fixtures — no
OCCT, no model file.

**Existing tests to retire with the code they cover:** `tests/test_cache.py:131`
and `:142` (`fn.call_key` contract), `:306-312` (revisit via `mesh_hits`, replaced
by law 7), and the `FrozenStats` helper at `:353` (drops the `mesh_hits` field).

## 6. Risks

1. **We depend on `ocp_tessellate` internals.** `instances[i]["cache_id"]`,
   `OcpObject.ref` and `OcpObject.name` are not public API. This is a real
   coupling cost, accepted because the alternative is reimplementing a mesh cache
   that already exists. Mitigation: pin the version (already policy per SPEC-ui
   risk 3) and test 5.6 catches drift on upgrade.
2. **The ambient-state dependency deepens.** The LRU is process-global, 256MB,
   tunable only via `OCP_CACHE_SIZE_MB`, and evicted independently of `Store`'s
   128-entry LRU. Meshes are large (~6.7MB for the grille payload), so roughly
   35–40 grille-sized meshes fit. SPEC-caching Phase 0 lesson 2 already flagged
   this dependency; this spec leans on it harder. If eviction skew becomes real,
   the fallback is our own mesh store — the design this spec reverses (§7), which
   remains implementable.
3. **Purity misclassification now reaches meshes.** A function wrongly judged
   cacheable would serve a stale mesh as well as stale geometry. Same blast
   radius as today — the geometry was already wrong — so no new failure mode, but
   worth naming.
4. **Parts that return a bare `Part` gain nothing.** `vent_grille` is registered
   with `@app.part()` and returns a `Part`, not a labeled `Compound`, so I4
   declines and it keeps today's behavior. Per-child meshing only helps parts
   that compose labeled children. This should be documented for model authors:
   returning `Compound(children=[...])` with labels is what buys drag speed.

## 7. Reversed from SPEC-caching Phase 3

| planned | status |
| --- | --- |
| Per-child tessellation loop in `builder.py` | **Cut.** `tessellate_group` already loops per instance. |
| `ChildMesh` type; JSON re-assembly (`assemble`, `union_bb`, ref remapping) | **Cut.** We never take the payload apart, so nothing needs stitching. |
| `Store.get_child_mesh` / `put_child_mesh`; `_Entry.part` becoming optional | **Cut.** Meshes never enter `Store`. The nullable-field problem disappears. |
| Per-node update message in `protocol.py` | **Deferred, not required.** The latency win is server-side and complete without it. Payload serialization measures ~13ms; browser parse and GPU upload remain unmeasured (SPEC-caching Phase 0 lesson 3). Measure before designing a message. |
| SPEC-ui revision for per-shape provenance `id`s | **Not needed now.** The `id` slot stays reserved for differential push. |

Also reversed from SPEC-caching Phase 1 (§3.5 here):

| established there | status |
| --- | --- |
| The mesh cache keyed on the part function's *call* key, checked before the build (Phase 0 lesson 1) | **Retired.** Its purpose was to skip a 3s build on revisit; with per-instance meshes a revisit rebuilds in 47ms, so the pre-build check is not worth a second key derivation. |
| S10 — "store injected at both `load_model` and `build_part`" | **Reversed.** Injected once, at `install_cache`. |
| `CacheEntry(part, mesh)` as one atomically-evicted entry | **Reversed.** Meshes leave `Store`; the entry collapses to the part. |

## 8. Punted

- **Drag-time `angular_tolerance`** with full-quality re-tessellation on release —
  the 5.4× lever from §2.1, and the only path to a sub-second `vent_dia`.
- **Recursive decomposition** for nested compounds. One level covers the reference
  model; `to_ocpgroup` flattens `bosses` (itself a `Compound`) into one instance.
- **Differential push** — sending only changed instances over the websocket.
  Gated on measuring browser-side cost.

## References

- `specs/SPEC-caching.md` — §2.1/§2.2 budgets, Phase 0 lessons, Phase 1 provenance layer
- `specs/SPEC-ui.md` — §6 protocol, `id` slot, viewer coupling
- `ocp_tessellate/tessellator.py:94` `make_key`, `:147` `cache`, `:567` `tessellate`
- `ocp_tessellate/convert.py:166` `create_cache_id`, `:218` `get_instance`, `:1892` per-instance quality, `:1904` the `tessellate` call
