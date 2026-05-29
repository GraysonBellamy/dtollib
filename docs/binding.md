# Binding contributor guide

This page explains how the `dtollib.capi` package binds the DataAcq
SDK and how to add or update bindings safely.  Read
[design.md §11](design.md) for the architectural overview and
[implementation-plan.md §1.4](implementation-plan.md) for the
verification gate.

## Three-layer C boundary

The C boundary lives in three modules:

| Layer | Module | Responsibility |
|---|---|---|
| 1 | [`dtollib.capi.prototypes`](api/capi.md#prototypes) | Raw `argtypes` / `restype` on each SDK function.  No state. |
| 2 | [`dtollib.capi.api`](api/capi.md#api) :: `OpenLayersApi` | Output-pointer extraction + `ECODE` → typed-exception classification.  One method per SDK function. |
| 3 | [`dtollib.backend.dataacq`](api/backend.md#dataacq) :: `DataAcqBackend` | Session orchestration — HDRVR refcount, capability cache, notification wrappers.  Never touches `ctypes` directly. |

The split is enforced by tests:

- `tests/unit/test_capi_api_check_invariant.py` AST-walks
  `OpenLayersApi` and fails if any method body lacks a `check(...)`
  call.
- `tests/unit/test_capi_prototypes.py` asserts `argtypes` /
  `restype` are set on every bound function.
- `tests/unit/test_capi_callbacks.py` enforces the §11.5 pointer-size
  hazard for `WPARAM` / `LPARAM` on all callback typedefs.

## Adding a new SDK function

Every new function follows the same protocol:

### 1. Locate the prototype in the installed headers

Open the relevant header (`OLDAAPI.H`, `OLMEM.H`, ...) in
`%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\`.  Find the
function declaration.  Note:

- `argtypes` element-by-element.  Pay special attention to:
    - `LPARAM` / `WPARAM` → MUST be `ctypes.wintypes.LPARAM` /
      `WPARAM` (pointer-sized).  Not `c_long` / `c_uint`.
    - Output pointers — `HDRVR *` is `POINTER(HDRVR)`.
    - String buffers — `LPSTR` is `c_char_p` (in-arg) or
      `create_string_buffer(N)` (out-arg).
- `restype` — almost always `ECODE`, but counter functions
  sometimes return `BOOL` (`c_int`).

### 2. Record the verification

Add a one-line entry in [decisions.md](decisions.md):

```text
2026-MM-DD — olDaFoo:
    argtypes verified against OLDAAPI.H rev X.Y.Z.
    restype = ECODE.
    Notes: <anything non-obvious>.
```

### 3. Add to `capi/prototypes.py`

Bind the function in the matching `declare_*` function:

```python
f = dll.olDaFoo
f.argtypes = [HDASS, c_uint, POINTER(c_ulong)]
f.restype = ECODE
```

Add the function name to the matching per-capability
`*_OLDAAPI_FUNCTIONS` tuple (or `*_OLMEM_FUNCTIONS`) so the regression
test covers it.

### 4. Add a typed method to `OpenLayersApi`

```python
def foo(self, hdass: int, knob: int) -> int:
    """Query the foo capability via olDaFoo."""
    out = c_ulong(0)
    status = self._dlls.oldaapi.olDaFoo(HDASS(hdass), knob, byref(out))
    check(self._dlls, status, op="olDaFoo", source="oldaapi")
    return int(out.value)
```

Critical: every method must route through `check`.  The AST test
fails if you forget.

### 5. Add an entry to `FakeDtolBackend` if needed

If the function is part of the runtime contract (not just a
diagnostic helper), extend `FakeDtolBackend` to enforce the same
ordering / capability rules.

### 6. Add tests

- `tests/unit/test_capi_prototypes.py` — extends automatically via
  the per-capability `*_OLDAAPI_FUNCTIONS` tuples.
- `tests/unit/test_capi_api_check_invariant.py` — extends
  automatically via the per-capability `*_method_names()` helpers.
- Add a method-specific unit test that exercises the success path
  against a pure-Python mock DLL.
- Add a binding test under `tests/binding/` that exercises the
  method against the stub DLL on a Windows runner.

## Header-diff tool

`scripts/gen_openlayers.py` parses installed SDK headers and diffs
them against the current bindings:

```pwsh
python scripts\gen_openlayers.py "C:\Program Files (x86)\Data Translation\Win32\SDK\Include"
```

Run after every SDK update.  Modes:

- `--check` (default) — exit non-zero on diff.
- `--report` — print the diff; exit zero.
- `--markdown` — render as a table for pasting into `decisions.md`.

The parser is regex-based — it handles the standard
`#define NAME value` and `OLSTATUS WINAPI Name(args)` patterns but
will produce spurious diffs against novel macro tricks in future SDK
releases.  When that happens, adjudicate by reading the header
directly and update `decisions.md`.

## Pitfalls

The big ones, in rough order of how often they bite:

- **`LPARAM` truncation** — using `c_long` where the SDK header says
  `LPARAM` silently truncates pointer-sized arguments on 64-bit
  Windows.  Always use `ctypes.wintypes.LPARAM`.
- **Forgetting `_check`** — the AST test catches this in CI but it
  is easy to forget when adding a method.
- **Capability-flag integer drift across SDK versions** — the
  numeric `OLSSC_*` IDs are not guaranteed stable.  The binding
  never compares an `OLSSC_*` value to a literal; it queries
  through the SDK and interprets the result.  Bench-verify each
  integer against the installed `OLDADEFS.H` and record in
  `decisions.md`.
- **Callback wrapper GC** — keeping a strong reference to every
  live `WINFUNCTYPE` wrapper is the responsibility of
  `DataAcqBackend`, not `OpenLayersApi`.  The notification bridge owns
  these references for the life of each registered subsystem; dropping
  one mid-run would let the GC reclaim the trampoline and crash the
  driver thread.
- **Header redistribution licensing** — Open Question 3 in
  [design.md §31](design.md) leaves SDK header redistribution
  unresolved.  Do not commit the headers into the repo until that
  question lands.
