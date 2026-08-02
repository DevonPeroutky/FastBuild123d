# Python standards

General Python and functional-design standards for this repo. These load
automatically whenever Claude reads a `.py` file anywhere under `libraries/`.
Infra-specific conventions (Modal, Jobs, the DAG orchestrator) live in
`libraries/infra/CLAUDE.md` and `.claude/rules/modal-infra.md`.

## Best Practices

- Comments: default to none — clear names and small functions should carry the
  meaning. Add one only for genuinely abnormal behavior or non-obvious domain
  knowledge, and keep it to 1–3 lines. Remove obvious/simple comments. See
  `libraries/infra/CONTRIBUTING.md`.
- Docstrings should be succinct and simple using familiar vocabulary over obtuse, academic sounding language.
- When writing tests, focus on the specific unique functionality under test and avoid testing generic logic.
- No lazy imports (unless absolutely necessary). Also include imports at the top
  of the file, which is standard practice.
- Do not tolerate circular dependencies or solve them using lazy loading or
  `TYPE_CHECKING` guards. If you detect a circular dependency, reconsider the
  organization of the code and base definitions.
- Isolate the parts of your system that transform data from the ones that use
  it. Data models outlive code.
- Coupling is the root of most evil.
- Make state explicit.
- Every piece of information should have a single source of truth.

# Functional Design Principles

---

## 1. Prefer Pure Functions

A function is pure if its output depends only on its inputs and it causes no observable side effects (no mutation, no I/O, no randomness). Pure functions are trivially testable, safe to parallelize, and safe to refactor.

**Do this:**

```python
def discount_price(price: float, rate: float) -> float:
    return price * (1 - rate)
```

**Not this:**

```python
discount_rate = 0.1  # hidden dependency

def discount_price(price: float) -> float:
    log_to_database(price)  # side effect
    return price * (1 - discount_rate)  # reads external state
```

**The test:** can you replace a function call with its return value everywhere in the program without changing behavior? If yes, it's pure.

---

## 2. Make Illegal States Unrepresentable

Model your data so that invalid combinations cannot exist at the type level. Use sum types (unions/enums) and product types (dataclasses/structs) to constrain your domain. This eliminates entire categories of bugs by construction rather than validation.

```python
from dataclasses import dataclass

# Bad: status is a bare string — any typo compiles fine
@dataclass
class Order:
    status: str  # "pending"? "shipped"? "shpped"?

# Good: each state carries exactly the data it needs
@dataclass
class Pending:
    items: list[Item]

@dataclass
class Shipped:
    items: list[Item]
    tracking_id: str

@dataclass
class Delivered:
    items: list[Item]
    tracking_id: str
    delivered_at: datetime

Order = Pending | Shipped | Delivered
```

**Principle:** if a field only makes sense in certain states, it should only exist in those states. Never use nullable fields as a proxy for state transitions.

---

## 3. Return Values for Errors — Don't Throw

Exceptions break normal control flow, hide failure modes from callers, and make composition difficult. Instead, encode the possibility of failure in the return type.

```python
def parse_age(raw: str) -> int | None:
    """Use None for 'might be absent' cases."""
    try:
        age = int(raw)
        return age if 0 < age < 150 else None
    except ValueError:
        return None

def parse_age_explained(raw: str) -> tuple[str, None] | tuple[None, int]:
    """Use an (error, None) | (None, value) pair when callers need failure context."""
    try:
        age = int(raw)
        if not (0 < age < 150):
            return (f"Age {age} out of range", None)
        return (None, age)
    except ValueError:
        return (f"'{raw}' is not a number", None)
```

**When to throw:** only for true programmer errors (assertion failures, invariant violations) — things that indicate bugs, not expected runtime conditions.

---

## 4. Keep Data Immutable by Default

Never mutate data that was passed to you. Build new values from old ones. Immutable data is safe to share across threads, trivially cacheable, and eliminates action-at-a-distance bugs.

```python
# Bad: caller's data silently mutated
def add_admin_role(user: dict) -> dict:
    user["roles"].append("admin")  # mutates the original
    return user

# Good: return a new structure
def add_admin_role(user: dict) -> dict:
    return {**user, "roles": [*user["roles"], "admin"]}
```

**Principle:** treat all inputs as read-only. If you need to change something, return a modified copy.

---

## 5. Separate Description from Execution

Build data structures that _describe_ what should happen, then write a separate function that _interprets_ them. This decouples intent from mechanism, making code testable, composable, and swappable.

```python
# Description: pure data representing planned actions
@dataclass
class SendEmail:
    to: str
    subject: str
    body: str

@dataclass
class WriteToDb:
    table: str
    record: dict

Action = SendEmail | WriteToDb

def plan_user_signup(email: str, name: str) -> list[Action]:
    """Pure function. No I/O. Easy to test."""
    return [
        WriteToDb("users", {"email": email, "name": name}),
        SendEmail(email, "Welcome!", f"Hi {name}, thanks for signing up."),
    ]

# Execution: impure interpreter — runs at the boundary
def execute(actions: list[Action]) -> None:
    for action in actions:
        match action:
            case SendEmail(to, subject, body): smtp_client.send(to, subject, body)
            case WriteToDb(table, record):     db.insert(table, record)
```

This pattern applies universally: job queues, API calls, file operations, CLI commands, test fixtures.

---

## 6. Push Side Effects to the Boundary

Structure programs as a pure functional core surrounded by a thin impure shell. The core computes, transforms, and decides. The shell reads input, calls the core, and writes output.

```
┌──────────────────────────────────┐
│          Impure Shell            │
│  (reads files, calls APIs, DB)   │
│                                  │
│   ┌──────────────────────────┐   │
│   │       Pure Core          │   │
│   │  (transforms, decides,   │   │
│   │   validates, computes)   │   │
│   └──────────────────────────┘   │
│                                  │
│  (writes output, sends emails)   │
└──────────────────────────────────┘
```

**Practical implication for agents:** when implementing a feature, write the logic as a pure function first, then wire it to I/O at the call site. This makes the logic trivially unit-testable without mocks.

---

## 7. Design with Small, Composable Units

Build complex behavior by combining small, single-purpose functions rather than writing large monolithic ones. Each function should do one thing and be reusable in contexts its author didn't anticipate.

**Composition checklist:**

- Can this function be broken into two that compose? If so, break it apart.
- Does this function both compute a result _and_ perform a side effect? Separate them.
- Does this function work on a concrete type when it could work on a generic one? Generalize it.

```python
# Instead of one big function:
def process_orders(orders):
    # validates, filters, transforms, saves, emails — 80 lines
    ...

# Compose small pieces:
def validate(order: Order) -> Order | None: ...
def apply_discount(order: Order) -> Order: ...
def to_invoice(order: Order) -> Invoice: ...

invoices = [
    to_invoice(apply_discount(o))
    for o in orders
    if validate(o) is not None
]
```

---

## 8. Use the Weakest Abstraction That Works

When choosing a pattern or interface, prefer the least powerful one that satisfies the requirements. More constrained abstractions are easier to reason about, test, optimize, and compose.

**Hierarchy of power (prefer top when possible):**

| If you need to...               | Use                                        |
| ------------------------------- | ------------------------------------------ |
| Transform values in a container | `map` (Functor)                            |
| Combine independent results     | `zip` / parallel composition (Applicative) |
| Chain dependent steps           | `flatMap` / `bind` / `and_then` (Monad)    |

**In practice:** if step B doesn't depend on the result of step A, don't sequence them with `flatMap` / chained `await` — process them independently. This communicates intent and enables parallelism.

```python
# Bad: false sequencing — b doesn't depend on a
a = await fetch_user(user_id)
b = await fetch_products()  # waits for a to finish for no reason

# Good: independent work is expressed as independent
a, b = await asyncio.gather(fetch_user(user_id), fetch_products())
```

---

## 9. Encode Invariants as Laws, Then Test Them

Every abstraction you build has implicit rules. Make those rules explicit and test them with property-based tests. This catches edge cases that example-based tests miss.

**Common laws to verify:**

- **Roundtrip:** `deserialize(serialize(x)) == x`
- **Idempotency:** `f(f(x)) == f(x)` (e.g., formatting, normalization)
- **Associativity:** `combine(combine(a, b), c) == combine(a, combine(b, c))`
- **Identity:** `combine(x, empty) == x`
- **Commutativity (when expected):** `merge(a, b) == merge(b, a)`

```python
from hypothesis import given, strategies as st

@given(st.text())
def test_json_roundtrip(s: str):
    assert json.loads(json.dumps(s)) == s

@given(st.lists(st.integers()))
def test_sort_idempotent(xs: list[int]):
    assert sorted(sorted(xs)) == sorted(xs)
```

**Principle for agents:** when you write a utility function, ask "what property must always hold?" and express it as a test.

---

## 10. Avoid Hidden State and Implicit Dependencies

Functions should receive everything they need through their arguments and return everything they produce through their return value. Avoid reading from or writing to global/module-level state, class instance variables used as scratch space, or environment variables buried deep in the call stack.

```python
# Bad: hidden dependency on global config and mutable cache
_cache = {}
def get_user(user_id: str) -> User:
    if user_id not in _cache:
        _cache[user_id] = db.fetch(user_id)  # hidden I/O + hidden mutation
    return _cache[user_id]

# Good: dependencies are explicit parameters
def get_user(user_id: str, *, db: Database, cache: dict[str, User]) -> tuple[User, dict[str, User]]:
    if user_id in cache:
        return cache[user_id], cache
    user = db.fetch(user_id)
    return user, {**cache, user_id: user}
```

**Principle:** if you can't tell what a function does from its signature alone, the signature is lying. Make dependencies explicit.

---

## 11. Leverage Exhaustive Pattern Matching

When branching on a value's type or variant, use pattern matching and ensure every case is handled. The compiler (or linter) should warn you when a new variant is added but not handled. This turns "forgot to handle the new case" from a runtime bug into a build-time error.

```python
from enum import Enum, auto

class Status(Enum):
    PENDING = auto()
    ACTIVE = auto()
    SUSPENDED = auto()

def status_label(s: Status) -> str:
    match s:
        case Status.PENDING:   return "Awaiting review"
        case Status.ACTIVE:    return "Live"
        case Status.SUSPENDED: return "On hold"
    # No default/wildcard — adding a new Status forces updating this function
```

**Principle:** avoid catch-all `else` / `default` / `case _` branches when matching on a known set of variants. A wildcard silently swallows new cases.

---

## Summary: Decision Checklist for Agents

Before writing or modifying code, run through these checks:

1. **Is this function pure?** If not, can the impure part be extracted to the caller?
2. **Is the data immutable?** Am I mutating an input when I should return a copy?
3. **Are errors in the return type?** Or am I throwing/raising for expected failures?
4. **Are illegal states possible?** Can I restructure the types to prevent them?
5. **Is description separated from execution?** Or is I/O tangled with logic?
6. **Is this the weakest abstraction that works?** Am I over-sequencing independent operations?
7. **Are invariants tested as properties?** Not just with a few hard-coded examples?
8. **Are all dependencies explicit in the signature?** No hidden globals, no ambient state?
9. **Is branching exhaustive?** No silent catch-all swallowing future variants?
10. **Can this be broken into smaller composable pieces?** One function, one job.
