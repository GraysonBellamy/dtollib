# `dtollib` Phased Implementation Plan

**Status:** Implementation plan (v0) — companion to `design.md`
**Created:** 2026-05-28
**Author:** Grayson Bellamy
**Scope:** Concrete, executable expansion of the §26 roadmap in `design.md`. Where `design.md` answers *what* `dtollib` is, this document answers *how it gets built, in what order, and what done looks like at each step.*

---

## 0. How to use this document

`design.md` is the architecture reference. This document is the work breakdown.
Every section here is a **workstream** with:

- **Goal** — one-sentence statement of intent.
- **Inputs** — what must be in place before starting.
- **Deliverables** — concrete files / functions / tests / docs that land.
- **Definition of done (DoD)** — verifiable acceptance bar.
- **Cross-refs** — `design.md` section + sibling repo precedent.
- **Effort** — rough sizing (S / M / L / XL) for planning order only.
- **Risk** — what can go wrong; mitigation lives in §13.

Effort key (assumes one engineer working with full sibling-library context):

| Size | Meaning             |
|------|---------------------|
| S    | < 1 day             |
| M    | 1–3 days            |
| L    | 3–10 days           |
| XL   | > 10 days / spans phases |

Sibling reference paths are absolute under `c:\Users\gbellamy\Documents\git\<sibling>` so they paste directly into `Read` / `Glob` / `Grep`.

---

## 1. Cross-cutting conventions (apply to every phase)

### 1.1 Source tree (target, end of v0.1)

Mirrors the v0.1 columns of `design.md` §6:

```
dtollib/
  src/dtollib/
    __init__.py, py.typed, version.py, _version.py
    config.py, errors.py, units.py, constants.py
    _logging.py, _runtime.py
    capi/        loader.py types.py prototypes.py constants.py
                 errors.py callbacks.py conversion.py api.py
    backend/     base.py dataacq.py fake.py
                 _buffer_pool.py _callback_bridge.py
    system/      discovery.py capabilities.py models.py
    channels/    base.py analog_input.py
    tasks/       spec.py builder.py session.py models.py
                 triggers.py metadata.py
    streaming/   block.py poll_source.py recorder.py _types.py
    sinks/       base.py _schema.py memory.py csv.py jsonl.py
                 sqlite.py parquet.py postgres.py raw_counts.py
    manager.py
    sync/        portal.py daq.py session.py recording.py
    cli/         list.py capture.py read.py info.py diag.py
    testing.py, utils.py
    tools/       replay_raw.py        # phase 3
  tests/
    conftest.py
    unit/        (50+ files at v0.1; see §15)
    integration/ (FakeDtolBackend end-to-end)
    binding/     (Windows-only, real ctypes vs stub DLL)
    hardware/    (opt-in, real DT9805/DT9806 attached)
    fixtures/    headers/  (SDK headers, gitignored unless §31.3 confirms redistribution)
                 fake_dlls/ (built with §22.3 toolchain)
  docs/         (Zensical + mkdocstrings)
  scripts/      gen_openlayers.py     # contributor diff tool
  .github/workflows/  ci.yml docs.yml release.yml hardware.yml
```

### 1.2 Type / lint / test discipline

- `pyproject.toml` is the **only** config surface; `ruff`, `mypy`, `pyright`, `pytest`, `coverage` all configured there.
- `mypy --strict` and `pyright --strict` both pass on every commit. No `Any`-leaks at module boundaries; ctypes internals may use `Any` inside `capi/` but must be re-typed at `OpenLayersApi` exit.
- Public dataclasses are always `frozen=True, slots=True, kw_only=True` (see `design.md` §8).
- Test naming: `tests/unit/test_<topic>.py`, one assertion concept per test, parametrised where it cuts duplication.
- Coverage gate: 90 % statement, 80 % branch on `src/dtollib/` excluding `cli/*`.

### 1.3 Working with the SDK on Windows

- Two DLLs (`oldaapi64.dll`, `olmem64.dll`) live under `%SystemRoot%\System32\` after a default DataAcq SDK install. The loader (§4) honours `DTOLLIB_OLDAAPI_DLL` / `DTOLLIB_OLMEM_DLL` overrides for development.
- Header files (`OLDAAPI.H`, `OLMEM.H`, `OLERRORS.H`, `OLTYPES.H`, `OLWIN.H`) live with the SDK install. Whether to copy them into `tests/fixtures/headers/` depends on the DT-Open Layers SDK license — Open Question 3 in `design.md` §31. Default: do not redistribute; CI diff check runs only on the maintainer Windows runner.
- 64-bit is the bring-up target (Open Question 2). The loader supports 32-bit but the bench, CI, and docs assume 64-bit.

### 1.4 Binding verification gate (applies to every phase that binds new SDK functions)

Before any new `olDa*` / `olDm*` function is added to `capi/prototypes.py`:

1. Locate the function's prototype in the installed `OLDAAPI.H` / `OLMEM.H` at `%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\`. **DO NOT** transcribe from `DTx-EZ\Include\OLDADEFS.bas` — the VB binding is ~25 years out of date and uses a different numbering family (0/1/2 vs 800/1001/1100). Every published SDK C example links against the C header. Bench-confirmed 2026-05-28: VB values are rejected by the live DLL.
2. Cross-check `argtypes` element-by-element (especially `LPARAM` vs `LONG` traps — see `design.md` §11.3 callout).
3. Confirm `restype` is `ECODE` (or whatever the header says — counter functions return `BOOL` in places).
4. Record the SDK version checked against in `docs/decisions.md` as a one-line entry: `2026-MM-DD — olDaFoo: argtypes verified against OLDAAPI.H rev 7.8.x`.
5. Add the function to the `scripts/gen_openlayers.py` diff allowlist.
6. Add a unit test in `tests/binding/test_signatures.py` (Phase 1+) that exercises the signature shape against the stub DLL.

This gate is what prevents the silent-divergence risk in `design.md` §27.

#### 1.4a Constant verification gate (applies to every phase that uses new SDK constants)

Equally important — and the root cause of the Phase 3 2026-05-28 bench failure: SDK constants ship in two files that disagree (C `OLDADEFS.H` vs VB `OLDADEFS.bas`). Constants added to `capi/constants.py` must:

1. Be transcribed from `OLDADEFS.H` (C), with the line number cited in the docstring.
2. Be bench-confirmed by reading them back via `olDaGetSSCaps` / `olDaGetDataFlow` / `olDaGetWrapMode` etc. on the maintainer bench, with the readback recorded in `docs/decisions.md` in the same commit.
3. Use the `OLSSC_*` enum-position (count from the `olssc_tag` declaration) — not a hex placeholder. The placeholder values used pre-2026-05-28 (`0x0001`, `0x0007`, `0x0100`, ...) silently mapped to *different* capability flags than intended.

A constant that's named but unverified is worse than one that's missing — code reads as if it works, while the SDK quietly returns wrong data.

### 1.5 FakeDtolBackend invariants

Every phase that adds SDK functions also adds matching `FakeDtolBackend` behaviour. The fake is not a stub — it enforces the same ordering and capability rules as the real SDK so unit tests catch the same bugs hardware would. The fake's invariant table is the canonical statement of contract; it grows monotonically across phases.

### 1.6 Definition of done — package-wide

A phase is **not complete** until:

- All listed SDK functions are bound and have binding tests.
- All listed `FakeDtolBackend` behaviours are implemented.
- All unit tests pass on Windows + Linux + macOS (where applicable).
- All hardware smoke tests pass on the maintainer bench (gated by `DTOLLIB_ENABLE_HARDWARE_TESTS=1`).
- All listed docs land at the documented `docs/` paths.
- All listed CLI entry-points work end-to-end against a real DT9805 (where applicable).
- `mypy --strict` and `pyright --strict` pass.
- `ruff check` and `ruff format --check` pass.
- Coverage gate (§1.2) holds.
- The phase's acceptance criterion from `design.md` §26 is demonstrably satisfied.

---

## 2. Phase 0 — Scaffold (no SDK, no hardware)

**`design.md` cross-ref:** §26 Phase 0.
**Goal:** Establish an importable, lintable, testable, CI-green package skeleton that compiles on Linux/macOS/Windows without the DataAcq SDK or any DT hardware. Every module that doesn't need the SDK gets ported from a sibling. The skeleton must be expressive enough that Phase 1 only adds the C boundary — not infrastructure.

### 2.1 Workstreams

#### 2.1.1 Repository initialisation (S)

**Deliverables:**

- `git init`, MIT `LICENSE`, `.gitignore` (port from `c:\Users\gbellamy\Documents\git\nidaqlib\.gitignore`).
- `README.md` stub matching `design.md` §29.
- `CHANGELOG.md` with a `## [Unreleased]` block.
- `CONTRIBUTING.md` pointing to design.md, this doc, and the §22 testing strategy.

**DoD:** Repo clones; pre-commit hook (ruff) runs; CI clones cleanly.

#### 2.1.2 `pyproject.toml` (S)

**Deliverables:** as sketched in `design.md` §24.1, with:

- `hatchling` + `hatch-vcs`.
- `requires-python = ">=3.13"`.
- Core deps `anyio>=4.13`, `numpy>=2`.
- Optionals: `postgres`, `parquet`, `docs`.
- Dev / lint / type / test / docs groups copied from `c:\Users\gbellamy\Documents\git\nidaqlib\pyproject.toml`.
- `[project.scripts]` lists all five CLIs (`dtol-discover`, `dtol-capture`, `dtol-diag`, `dtol-read`, `dtol-info`) but only the first three resolve in Phase 0–1. The two v0.2 entries point at stubs that print "not yet implemented" until Phase 4 lands.
- Tool configuration: `[tool.ruff]`, `[tool.mypy]`, `[tool.pyright]`, `[tool.pytest.ini_options]`, `[tool.coverage.run]`.

**DoD:** `python -m build` produces a clean sdist + wheel; `twine check --strict dist/*` is green; `uv pip install -e .` succeeds on Windows / Ubuntu / macOS.

#### 2.1.3 Port shared infrastructure (M)

Direct ports from `c:\Users\gbellamy\Documents\git\nidaqlib\src\nidaqlib\` (rename `nidaqlib` → `dtollib`, `NIDaq*` → `Dtol*`, `nidaqlib.*` log roots → `dtollib`):

| Target file              | Source (nidaqlib)             | Notes |
|--------------------------|-------------------------------|-------|
| `_logging.py`            | `_logging.py`                 | `ROOT = "dtollib"` |
| `_runtime.py`            | `_runtime.py`                 | `eager_task_factory` installer |
| `version.py`             | `version.py`                  | Re-export of `_version.__version__` |
| `_version.py`            | (hatch-vcs generated)         | `.gitignore` it; never hand-edit |
| `py.typed`               | empty file                    | — |
| `units.py`               | `units.py`                    | `to_pint` helper |
| `sync/portal.py`         | `sync/portal.py`              | `SyncPortal` — pure port |
| `sinks/_schema.py`       | `sinks/_schema.py`            | Row helpers — pure port |
| `sinks/memory.py`        | `sinks/memory.py`             | `InMemorySink` — pure port |
| `sinks/base.py`          | `sinks/base.py`               | Protocols — pure port |
| `streaming/_types.py`    | `streaming/_types.py`         | `ErrorPolicy`, `OverflowPolicy`, `AcquisitionSummary` — see §1.6 note re renaming `errors_observed` semantics |

**Why these and not more?** Anything that touches the SDK or a DT-specific type stays in later phases. The list above is the *largest* set of modules that can be ported without any DT awareness.

**DoD:** Ports compile; tests for ported modules pass on all three OSes (lift the relevant `nidaqlib` unit tests verbatim and rename).

#### 2.1.4 `config.py` + `DtolConfig` + `config_from_env` (S)

**Deliverable:** `src/dtollib/config.py` per `design.md` §19.1. Fields specific to dtollib:

- `default_buffers: int = 4` (new vs nidaqlib).
- `oldaapi_dll_path: str | None = None`.
- `olmem_dll_path: str | None = None`.

`config_from_env` reads `DTOLLIB_*` env vars; mirrors `nidaqlib.config_from_env`.

**DoD:** Unit tests cover default values, env-var parsing, `replace()` immutability; mirrors `c:\Users\gbellamy\Documents\git\nidaqlib\tests\unit\test_config.py`.

#### 2.1.5 `errors.py` with the full `DtolError` hierarchy (M)

**Deliverable:** `src/dtollib/errors.py` exposing every class in `design.md` §17.3, plus `ErrorContext` per §17.2.

This is *forward-defined* in Phase 0 — most subclasses will not be raised until later phases — but the hierarchy is finalised now so downstream phases don't need to add error classes that ecosystem consumers depend on. Concretely: every class in the §17.3 tree is present and importable. None of them are raised by code in Phase 0.

**DoD:** `from dtollib import DtolError, DtolBufferOverrunError, ...` works; type-check passes; a unit test asserts `issubclass(DtolBufferOverrunError, DtolCapiError)` and the other documented relationships.

#### 2.1.6 `constants.py` (S)

**Deliverable:** Phase-0 stub. Phase 0 needs only the public re-export shape (`DataFlow`, `SubsystemType`, `Edge`, etc. — the StrEnums declared in `design.md` §8). SDK-level constants (`OL_DF_*`, `OLSS_*`, `OLDA_WM_*`) land in `capi/constants.py` during Phase 1.

The split is intentional: `dtollib.constants` is the user-facing surface, `dtollib.capi.constants` is the binding-internal surface. They never share a name.

**DoD:** All StrEnums from `design.md` §8.12, §8.13, §8.14, §8.15 are importable.

#### 2.1.7 CI workflows (M)

Mirror `c:\Users\gbellamy\Documents\git\nidaqlib\.github\workflows\`:

| Workflow        | Phase 0 scope                                                                  |
|-----------------|--------------------------------------------------------------------------------|
| `ci.yml`        | lint (Ubuntu), typecheck (Ubuntu), test matrix (Win + Ubuntu + macOS, py3.13 + py3.14) on unit tests only |
| `docs.yml`      | Build Zensical docs; deploy on `main` push                                     |
| `release.yml`   | Tag-triggered build + PyPI publish + GitHub release                            |
| `hardware.yml`  | Phase 1+; gated `workflow_dispatch`; runs on self-hosted Windows runner        |

`hardware.yml` lands in Phase 1 (it needs a real DT device for any green run). In Phase 0 it can be present but skipped.

**DoD:** All three `main` workflows run green on first PR.

#### 2.1.8 Docs scaffolding (S)

`docs/index.md` + Zensical config (`mkdocs.yml`-equivalent) ported from `c:\Users\gbellamy\Documents\git\nidaqlib\`. Most `docs/*.md` files are Phase 1+; Phase 0 produces `index.md` + a stubbed `installation.md` that says "SDK install steps land in Phase 1".

**DoD:** `mkdocs build --strict` (or Zensical equivalent) runs green.

#### 2.1.9 Smoke-test suite (S)

`tests/unit/test_smoke.py`:

- `import dtollib` succeeds on every OS.
- `dtollib.__version__` is a non-empty string.
- `dtollib.DtolError` is importable; raising it preserves `ErrorContext`.
- `DtolConfig()` constructs with documented defaults.
- All eight `*Error` subclasses can be instantiated.

**DoD:** All five OS × Python combinations pass.

### 2.2 Phase 0 acceptance

Repository state after Phase 0:

1. `uv pip install -e .` works on Windows, Ubuntu, macOS with no DT SDK present.
2. Import-smoke tests pass on all CI matrix entries.
3. `dtollib.errors` has every subclass listed in `design.md` §17.3.
4. `DtolConfig` accepts and validates every documented env var.
5. CI is green; coverage gate satisfied (mostly via ported tests).
6. No SDK function is bound. `find_devices()` does not exist yet. `capi/` directory does not exist yet.

**Bottom-line check:** if a Phase 1 implementer reads only design.md plus this section, they can begin Phase 1 the same day.

---

## 3. Phase 1 — C boundary, discovery, diagnostics

**`design.md` cross-ref:** §26 Phase 1, §10.3 (three-layer C boundary), §11 (capi internals), §17.4 (error wrapping).
**Goal:** Build the entire `capi/` package, the `OpenLayersApi` layer, and a Phase-1-scoped `DataAcqBackend` + `FakeDtolBackend`. Enable discovery (`find_devices()`, `find_subsystems()`), capability query (`CapabilitySet`), and the install-troubleshooting CLI (`dtol-diag`). End-state: the bench can run `dtol-diag` and `dtol-discover` against a real DT9805 and see useful output.

**Bound SDK functions (15 — exactly the §26 list):**
`olDaGetVersion`, `olDmGetVersion`, `olDaGetErrorString`, `olDmGetErrorString`, `olDaEnumBoards`, `olDaEnumBoardsEx`, `olDaGetBoardInfo`, `olDaInitialize`, `olDaTerminate`, `olDaEnumSubSystems`, `olDaGetDevCaps`, `olDaGetDASS`, `olDaReleaseDASS`, `olDaGetSSCaps`, `olDaGetSSCapsEx`, `olDaEnumSSCaps`, `olDaEnumChannelCaps`.

### 3.1 Header verification gate (S, blocker for the rest of Phase 1)

Per `design.md` §26 Phase 1 (Header verification prerequisite) and §1.4 above.

**Deliverables:**

- `docs/decisions.md` created with the prologue from design.md §11.7 (hand-curated is source of truth).
- One entry per type alias from `capi/types.py` (HDRVR, HDASS, HBUF, HLIST, HSSLIST, ECODE, OLSTATUS), each annotated with the header it was checked against and the SDK version.
- One entry per Phase-1 function listing the verified `argtypes` and `restype`.

**DoD:** Every type alias and every prototype in §3.2 / §3.3 has a corresponding `docs/decisions.md` entry. The maintainer signs off in writing in the same commit.

### 3.2 `capi/loader.py` — two-DLL discovery (M)

**Deliverable:** `src/dtollib/capi/loader.py` implementing `load_openlayers()` per `design.md` §11.1.

Important details:

- Two independent resolution chains (explicit path → env var → default install path → bare `WinDLL` name). Each chain runs separately for `oldaapi*.dll` and `olmem*.dll`.
- Bitness detection via `struct.calcsize('P') * 8`.
- Default install paths per bitness:
  - 64-bit: `%SystemRoot%\System32\oldaapi64.dll`, `%SystemRoot%\System32\olmem64.dll`.
  - 32-bit on 64-bit Windows: `%SystemRoot%\SysWOW64\oldaapi32.dll`, `%SystemRoot%\SysWOW64\olmem32.dll`.
- `OpenLayersDlls` frozen dataclass holding both `WinDLL` handles + paths + bitness.
- Failure → `DtolDependencyError` with the full candidate list; logs the loaded path + bitness at INFO on success.

**Tests:**

- `tests/unit/test_loader.py`: non-Windows raises immediately with a clear platform message.
- `tests/binding/test_loader.py` (Windows-only): loads the real DLLs against a known install; loader honours both env-var overrides.
- Mock-loader fixture in `conftest.py` for downstream tests that need to pretend the load succeeded.

**Risk:** Bitness mismatch (32-bit Python + 64-bit DLL) is a common failure mode and the OS error from `ctypes.WinDLL` is unhelpful. The loader pre-checks bitness and raises before `WinDLL` does.

### 3.3 `capi/types.py`, `prototypes.py`, `constants.py` (M)

**Deliverables:**

- `capi/types.py`: opaque type aliases per `design.md` §11.2. All are `c_void_p` unless §3.1 found otherwise.
- `capi/prototypes.py`: two declaration functions, `declare_oldaapi(dll)` and `declare_olmem(dll)`. Phase 1 binds the 15 functions in the SDK-functions list at the top of §3. Each function call sets `argtypes` and `restype` explicitly.
- `capi/constants.py`: All SDK constants referenced in Phase 1's surface — `OLSS_AD`, `OLSS_DA`, `OLSS_DIN`, `OLSS_DOUT`, `OLSS_CT`, `OLSS_QUAD`, `OLSS_TACH`, plus the `OLSSC_*` capability flags consulted by `query_capabilities` (RETURNS_FLOATS, SUP_SINGLEVALUE, SUP_CONTINUOUS, SUP_SIMULTANEOUS_SH, SUP_MULTISENSOR, SUP_DMA, NUMCHANNELS, CGLDEPTH, IO_TYPE, etc.).

Each constant carries a docstring naming the header it came from (`OLDAAPI.H`, `OLMEM.H`, `OLERRORS.H`, `OLWIN.H`).

**Tests:**

- `tests/binding/test_signatures.py`: parametrised over every declared function; asserts (a) `argtypes` is a tuple (not None), (b) `restype` is `ECODE` (or the header-documented exception), (c) the function symbol exists on the loaded DLL.
- `tests/binding/test_constants.py`: parametrised over every declared constant; asserts the value matches the value in the SDK header (header lookup via a contributor-only fixture, gated to the Windows runner with the SDK installed).
- `tests/unit/test_constants_smoke.py`: cross-platform import-smoke; values are checked by the Windows binding lane.

**Effort note:** transcription is mechanical but adversarial — every `LPARAM`-vs-`LONG` mistake is a silent-data-corruption bug. The header-diff script (§3.10) catches these mechanically.

### 3.4 `capi/errors.py` — two-DLL error classification (M)

**Deliverables:**

- `oldaapi_error_string(status)` and `olmem_error_string(status)` — call the right DLL's error-string function. Each takes the relevant `WinDLL` reference (passed in via `OpenLayersDlls`).
- `classify(status: int) -> type[DtolError]` — the per-code table from `design.md` §17.4. The Phase 1 deliverable is the per-code table sourced from `OLERRORS.H`, with the range table retained as fallback per §17.4.
- `check(status, *, op, source, **ctx)` — wraps `classify` + error-string + `ErrorContext` construction. This is the single error-wrapping point.

**Tests:**

- `tests/unit/test_error_classify.py`: parametrised over every documented `OLERR_*` code → expected subclass. Failing this means a future SDK code addition needs a corresponding table entry.
- Round-trip test for `ErrorContext` carrying `ecode_source` correctly.

**Risk:** range-based classification is fragile (`design.md` §17.4 callout). The Phase 1 *requirement* is the per-code table; the range table is a fallback for unknown codes only.

### 3.5 `capi/callbacks.py`, `capi/conversion.py` — defined now, used later (S)

`callbacks.py` lands in Phase 1 because `NOTIFY_PROC` is referenced by `olDaSetNotificationProcedure` signature (which is bound in Phase 3, but the type wants to be defined exactly once). It is **declared but not exercised** in Phase 1.

`conversion.py` lands in Phase 1 only insofar as `detect_returns_floats(dll, hdass)` is needed by `query_capabilities` to populate the `CapabilitySet.returns_floats` field. The vectorised code-to-volts and CJC deinterleave functions land in Phase 3.

**Tests:** `tests/unit/test_callback_typedef.py` — assert `NOTIFY_PROC` uses `WINFUNCTYPE` (not `CFUNCTYPE`) and the wparam/lparam typedefs are pointer-sized (`wintypes.WPARAM` / `wintypes.LPARAM`, not `c_uint` / `c_long`). This is the §11.5 callout, made into a regression test.

### 3.6 `capi/api.py` — `OpenLayersApi` (L)

**Deliverable:** `OpenLayersApi` class wrapping `OpenLayersDlls` with one method per Phase-1 SDK function. Each method:

1. Allocates output-pointer storage (e.g. `hdass = HDASS()`).
2. Calls the prototype with `byref` where needed.
3. Calls `_check(status, op=..., source=..., **ctx)`.
4. Returns the extracted output as a plain int / str / sequence.

`OpenLayersApi` does **no** state caching, no buffer-pool, no notification wrapper management. It is a pure call shape.

**Tests:**

- `tests/binding/test_api_against_stub.py` (Windows-only): runs against a stub DLL built from `tests/fixtures/fake_dlls/oldaapi_stub.c` that re-exports the SDK header values and emulates the minimal call shape (return success, populate output pointers with deterministic values). Asserts every method extracts outputs correctly and propagates errors correctly.
- `tests/unit/test_api_mocked.py`: pure-Python mock for `OpenLayersDlls` exercising the same call shape; cross-platform.

**Risk:** The output-pointer extraction is error-prone. The stub DLL approach is critical here — see §22.3 of `design.md` for the harness. The bigger risk is forgetting to call `_check` in a new method — guarded by a `tests/unit/test_api_all_methods_check.py` that introspects the class and asserts every public method's body contains a `_check` call (AST-level check on the source).

### 3.7 `backend/base.py`, `backend/dataacq.py` — Phase 1 subset (L)

**Deliverables:**

- `backend/base.py`: full `DtolBackend` Protocol per `design.md` §10.2. The full Protocol surface ships in Phase 1 (it's small, frozen-shape, and downstream phases need the method names to exist so type-checking incremental code is sensible). Methods unused in Phase 1 are present as Protocol declarations only — `FakeDtolBackend` raises `NotImplementedError` if they're called before the relevant phase lands.
- `backend/dataacq.py`: `DataAcqBackend` implementing the Phase-1 subset (initialize, terminate, get_dass, release_dass, get_version, enum_boards, enum_subsystems, query_capabilities, get_state). Methods outside the Phase-1 subset raise `NotImplementedError` with a clear "lands in Phase N" message.

`DataAcqBackend` construction:

```python
class DataAcqBackend:
    def __init__(self, dlls: OpenLayersDlls | None = None) -> None:
        self._dlls = dlls or load_openlayers()
        self._api = OpenLayersApi(self._dlls)
        self._capabilities_cache: dict[int, CapabilitySet] = {}
        self._notification_wrappers: dict[int, Any] = {}  # used in Phase 3
        ...
```

**Tests:**

- `tests/unit/test_dataacq_backend_phase1.py`: every Phase-1 method exercised against a mocked `OpenLayersApi`.
- `tests/binding/test_dataacq_real.py` (Windows + DT hardware): smoke against a real DT9805.

### 3.8 `backend/fake.py` — `FakeDtolBackend` (Phase 1 surface) (L)

**Deliverables:**

- `FakeDtolBackend` implementing the same Phase-1 Protocol subset.
- Scriptable boards: `FakeDtolBackend(boards=(BoardInfo(name="DT9805(00)", model="DT9805", ...),))`.
- Scriptable subsystems with custom `CapabilitySet`s.
- Operation log: `fake.operations: list[tuple[str, object]]` (matches `design.md` Appendix B shape).
- Synthetic ECODE injection: `fake.fail_next("olDaInitialize", code=200800)`.
- `OLSSC_RETURNS_FLOATS` per-subsystem toggle (so the float-vs-int dispatch path is testable in Phase 2+).
- Construction helpers in `dtollib.testing`: `make_fake_dt9805()`, `make_fake_dt9806()` that pre-populate realistic capability sets — these are the test ergonomics that let downstream tests stay short.

**Tests:** `tests/unit/test_fake_phase1.py` exercises every helper. The fake's behaviour is locked by its own tests so Phase 2+ tests can rely on it.

### 3.9 `system/` — discovery + capabilities (M)

**Deliverables:**

- `system/models.py`: `BoardInfo`, `SubsystemInfo`, `DeviceInfo` per `design.md` §20.2.
- `system/discovery.py`: `find_devices()` and `find_subsystems(board)`, both async, both pure wrappers around the backend.
- `system/capabilities.py`: `CapabilitySet` dataclass (immutable; field-per-capability), plus `query_capabilities(hdass) -> CapabilitySet`. The class exposes helpers like `io_type_for_channel(ch)`, `supports_continuous`, `supports_simultaneous_sh`, etc., so downstream phases never need to inspect raw flag bitmasks.

`CapabilitySet` is populated from `olDaGetSSCaps` + `olDaGetSSCapsEx` + `olDaEnumSSCaps` + `olDaEnumChannelCaps`. The composition order matters (`olDaGetSSCaps` first establishes which downstream cap calls are valid); `query_capabilities` documents this.

**Tests:**

- `tests/unit/test_discovery.py`: `find_devices()` against a fake with two boards; both reported.
- `tests/unit/test_capabilities.py`: `CapabilitySet` constructed from synthetic flag bitmaps; helpers return the right values.
- `tests/integration/test_discovery_e2e.py`: full open-device-fake → find → inspect lifecycle.
- `tests/hardware/test_discovery_real.py`: real DT9805 reports the expected model, instance, and capability flags.

### 3.10 `scripts/gen_openlayers.py` — header diff (M)

**Deliverable:** Python script that, given the path to an installed `OLDAAPI.H` + `OLMEM.H` + `OLERRORS.H`, parses out function prototypes and constant `#define`s, and prints a diff against the hand-curated `capi/prototypes.py` + `capi/constants.py`.

Output modes:

- `--check` (default): exit 0 if no diff, 1 otherwise. CI mode.
- `--report`: print the diff; exit 0.
- `--report --markdown`: print the diff as a markdown table for `docs/decisions.md` updates.

The parser does not need to be a full C parser — a regex over function decls and `#define`s is enough for this surface. The script is run on the maintainer Windows machine when an SDK update lands; CI runs it if/when `tests/fixtures/headers/` is populated (Open Question 3 outcome).

**Tests:** `tests/unit/test_gen_openlayers.py` — feed synthetic header text; assert the diff output matches expectation.

### 3.11 `cli/diag.py` — `dtol-diag` (M)

Per `design.md` §21.3. This is the first user-facing surface and the first place install troubleshooting lands.

**Deliverable:** `cli/diag.py` with `main()` entry. Subcommands:

- `dtol-diag` (default = all checks).
- `dtol-diag sdk` — just the DLL + version section.
- `dtol-diag boards` — just enumeration.
- `dtol-diag --json` — machine-readable.

Reports per §21.3.

**Tests:** `tests/unit/test_cli_diag.py` runs the CLI against `FakeDtolBackend` injected via `--backend fake` flag (hidden flag used by tests).

### 3.12 `cli/discover.py` — `dtol-discover` (S)

Per `design.md` §21.1.

**Deliverable:** `cli/discover.py` with arg parsing (`--board`, `--json`, default summary table).

**Tests:** parallel to `test_cli_diag.py`.

### 3.13 `utils.py` — pure-Python helpers (M)

Per `design.md` §15.4. These don't need the SDK:

- `convert_temperature_to_volts(tc_type, temperature_c)` — NIST ITS-90 polynomials.
- `convert_volts_to_temperature(tc_type, volts, cjc_temperature_c=0.0)` — inverse polynomial + CJC correction.
- `get_thermocouple_range(tc_type)` — documented operating range tuple per TC type.
- `compute_rectangular_rosette(eps_0, eps_45, eps_90)` and `compute_delta_rosette(eps_0, eps_60, eps_120)`.

**Tests:** `tests/unit/test_utils.py` — parametrised against hand-calculated NIST reference vectors (one row per TC type at a few known temperatures) and textbook rosette examples. Runs on Linux/macOS CI without the SDK.

**Risk:** NIST polynomial coefficients are notoriously easy to transcribe wrong. The test suite is the safety net. Source the coefficients from the published NIST monograph 175 (Type J, K, T, E, R, S, B, N) and cite the source in the docstring.

### 3.14 Documentation deliverables for Phase 1

| Doc                         | Content                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `docs/installation.md`      | Real install steps: SDK download, DLL paths, env-var overrides, common failures |
| `docs/troubleshooting.md`   | `dtol-diag` output decoded; common ECODE errors                          |
| `docs/binding.md`           | Contributor doc: capi/* layout, header verification process, gen_openlayers.py |
| `docs/decisions.md`         | Type-alias verifications + Phase-1 prototype verifications + Open Question resolutions |

### 3.15 Phase 1 acceptance criteria

Mirrors `design.md` §26 Phase 1 acceptance, expanded:

1. `dtol-diag` reports cleanly on a fresh DT-Open Layers SDK install.
2. `find_devices()` returns a populated list against a connected DT9805 *and* a DT9806 (Open Question 4 — confirm actual board name strings, e.g. `DT9805(00)`).
3. `CapabilitySet` correctly populates `OLSSC_RETURNS_FLOATS = true` for DT9805/DT9806 multi-sensor subsystems and `OLSSC_SUP_MULTISENSOR = true`.
4. Full Phase-1 unit + binding test suite is green on Windows CI.
5. Non-Windows CI runs the discovery suite against `FakeDtolBackend` and reports clean failure (`DtolDependencyError`) when `load_openlayers()` is called.
6. `docs/decisions.md` has one entry per Phase-1 type alias and one per Phase-1 prototype, each with SDK version.
7. `dtol-discover --json` output parses; `dtol-diag --json` output parses.
8. `dtollib.utils` functions pass against hand-calculated NIST and textbook references on Linux CI.

**Output of Phase 1:** A user with a fresh DT-Open Layers SDK install and a DT9805 plugged in can `pip install dtollib`, run `dtol-diag`, and see a clean report. They cannot yet *acquire data* — that's Phase 2 — but they can confirm the binding works.

---

## 4. Phase 2 — Single-value analog input (DT9805 happy path)

**`design.md` cross-ref:** §26 Phase 2, §13.1 (single-value mode), §8.5a (MULTI_SENSOR ordering), §9 (session model).
**Goal:** Bring up the on-demand scalar read path. A user can construct a `TaskSpec` with `AnalogInputVoltage` or `ThermocoupleInput` channels, open a session, and call `await session.poll()` to get a `DaqReading`. The DtolManager handles multi-device polling. Sync facade works.

**Bound SDK functions (12):** `olDaSetDataFlow`, `olDaSetChannelType`, `olDaSetChannelRange`, `olDaSetRange`, `olDaSetGainListEntry`, `olDaSetMultiSensorType`, `olDaSetThermocoupleType`, `olDaSetReturnCjcTemperatureInStream`, `olDaConfig`, `olDaGetSSState`, `olDaGetSingleValue`, `olDaGetSingleFloat`, `olDaGetSingleValueEx`, `olDaGetSingleValues`, `olDaGetSingleFloats`, `olDaGetCjcTemperature`, `olDaGetCjcTemperatures`, `olDaCodeToVolts`, `olDaVoltsToCode`.

### 4.1 Channel specs (M)

**Deliverable:** `src/dtollib/channels/base.py` + `channels/analog_input.py` containing:

- `ChannelSpec` base (`design.md` §8.2).
- `AnalogInputBase` (§8.3).
- `AnalogInputVoltage` (§8.4).
- `ThermocoupleInput` (§8.5).
- Enums: `ChannelType`, `FilterType`, `Encoding`, `CouplingType`, `ThermocoupleType`, `CjcSource`, `TemperatureUnit`.

`__post_init__` validation on `ThermocoupleInput`: assert `(min_val_degc, max_val_degc)` fits inside `dtollib.utils.get_thermocouple_range(self.thermocouple_type)`. Saves an SDK round-trip and produces a precise client-side error.

The other AI subclasses (`RtdInput`, `ThermistorInput`, `StrainInput`, `BridgeInput`, `IepeInput`) **defer to Phase 6**. They're listed in the file header as "implemented in Phase 6" so contributors don't add them prematurely.

**Tests:** `tests/unit/test_channel_specs.py` — construction, kw-only enforcement, validation rejecting bad ranges, `to_dict`/`from_dict` round-trip via discriminator.

### 4.2 `TaskSpec` + `Timing` (M)

**Deliverable:** `src/dtollib/tasks/spec.py` per `design.md` §8.1 + §8.7.

Phase 2 only uses `TaskSpec.timing = None` (single-value mode), `TaskSpec.data_flow = DataFlow.SINGLE_VALUE`, and trivial `TaskSpec.trigger = SoftwareStart()`. But the full dataclass is implemented now so Phase 3 doesn't have to add fields.

`Timing` is implemented in full (Phase 3 uses it). Validation rule: `Timing` is required if `data_flow != DataFlow.SINGLE_VALUE`, forbidden otherwise. Mismatch raises `DtolValidationError`.

**Validation matrix (`tasks/spec.py:__post_init__`):**

| `data_flow`         | `timing` allowed? | `trigger` default       | `buffers` required? |
|---------------------|-------------------|-------------------------|---------------------|
| `SINGLE_VALUE`      | no                | `SoftwareStart()` (implicit) | no                  |
| `CONTINUOUS`        | yes (required)    | `SoftwareStart()`       | yes                 |
| `FINITE`            | yes (required)    | `SoftwareStart()`       | yes                 |
| (`*_PRETRIGGER`, `*_ABOUT_TRIGGER`) | yes  | `SoftwareStart()`       | yes                 |

Mixing channel kinds in `TaskSpec.channels` raises `DtolValidationError` (per `design.md` §8.1 — one HDASS = one subsystem type).

### 4.3 `TaskBuilder` — Phase 2 subset (M)

**Deliverable:** `src/dtollib/tasks/builder.py`. The builder is the single place that walks the channel list and issues backend calls in the correct order. Phase 2 single-value sequence:

```
backend.set_data_flow(hdass, OL_DF_SINGLEVALUE)
for ch_index, channel in enumerate(spec.channels):
    if caps.io_type_for_channel(channel.physical_channel) == IOType.MULTI_SENSOR:
        backend.set_multi_sensor_type(hdass, channel.physical_channel, channel.io_type)
    backend.set_channel_type(hdass, channel.physical_channel, channel.channel_type)
    backend.set_channel_range(hdass, channel.physical_channel, channel.min_val, channel.max_val)
    backend.set_gain_list_entry(hdass, ch_index, channel.physical_channel, channel.gain)
    if isinstance(channel, ThermocoupleInput):
        backend.set_thermocouple_type(hdass, channel.physical_channel, channel.thermocouple_type)
backend.set_stop_on_error(hdass, spec.stop_on_error)
backend.commit(hdass)   # olDaConfig
```

**Critical:** the `set_multi_sensor_type` call **must** come before any other per-channel setter on `MULTI_SENSOR` channels (`design.md` §8.5a — silent-wrong-data bug if missed). The builder enforces this unconditionally on `MULTI_SENSOR` channels; the `FakeDtolBackend` rejects out-of-order calls.

**Tests:** `tests/unit/test_task_builder_ordering.py` — assert the call sequence on a `MULTI_SENSOR` board matches the spec; assert reorder attempts on the fake raise `DtolTaskStateError`.

### 4.4 `DtolSession` — Phase 2 subset (L)

**Deliverable:** `src/dtollib/tasks/session.py` per `design.md` §9.1.

Phase 2 methods: `__init__`, `prepare`, `commit`, `configure` (= prepare + commit), `start`, `stop`, `abort`, `poll`, `close`, `is_running`, `state` property, `raw_hdass`, `raw_hdrv`, `backend` properties, `__aenter__`, `__aexit__`.

> `diag_read_reg` / `diag_write_reg` are **descoped from v0.1** (B2 in
> `plan-hardware-functional.md`): `olDaReadDevReg`/`olDaWriteDevReg` are
> unbound with no bench-verified DLL export. Raw register access goes
> through the `raw_hdass` / `raw_hdrv` / `backend` escape hatches.

Methods deferred to later phases (`read_block`, `read_inprocess`, `write`, `queued_buffer_dones`) raise `NotImplementedError` with phase pointer.

Lifecycle invariants per `design.md` §9.2 — Phase 2 enforces:

- `SubsystemState` transitions: `INITIALIZED → CONFIGURED_FOR_SINGLE_VALUE → RUNNING → IO_COMPLETE → INITIALIZED`.
- `poll()` raises `DtolTaskStateError` if `state != RUNNING` (well, more specifically if `state` is `STOPPING`/`ABORTING`/`INITIALIZED`).
- `close()` is idempotent.
- HDRVR refcount across sessions (one `olDaInitialize` per board).

`poll()` implementation:

1. Acquire session lock (`anyio.Lock`).
2. Branch on `OLSSC_SUP_SIMULTANEOUS_SH`: if present, single `olDaGetSingleValues` / `olDaGetSingleFloats` call across all channels; else loop per-channel `olDaGetSingleValue` / `olDaGetSingleFloat`.
3. Branch on `OLSSC_RETURNS_FLOATS`: if true, values are already engineering units; else convert via `capi.conversion.codes_to_volts_vectorised` on the scalar.
4. For TC channels: detect sentinel via `capi.conversion.detect_thermocouple_sentinel`; populate `sensor_status` and `data[ch] = NaN` for sentinel positions.
5. Construct `DaqReading` with `requested_at` / `received_at` / `t_utc` / `t_mono_ns` / `latency_s` filled.

**Tests:**

- `tests/unit/test_session_lifecycle.py`: SubsystemState transitions; idempotent close; close-while-running aborts; configure failure tears down.
- `tests/unit/test_session_poll.py`: poll across MULTI_SENSOR + voltage + TC channels; assert simultaneous-vs-loop dispatch; assert sentinel detection populates `sensor_status` and NaN-fills.
- `tests/unit/test_session_poll_invalid_state.py`: assert poll() during STOPPING raises with a state-aware error message.

### 4.5 `tasks/models.py` — DaqReading (S)

**Deliverable:** `DaqReading` per `design.md` §8.9. `DaqBlock` and `DaqSample` defer to Phase 3.

`SensorStatus` enum lives here (or `tasks/models.py`-adjacent).

**Tests:** `tests/unit/test_models.py` — construction, immutability, `to_dict` shape (Phase 2 is the first to need JSON-serialisable readings for the sinks).

### 4.6 `tasks/triggers.py` — `SoftwareStart` only (S)

Just enough trigger surface to make `TaskSpec.trigger=SoftwareStart()` round-trip. Full hierarchy (`ExternalDigitalStart`, `AnalogThresholdStart`, `SyncBusStart`, `ReferenceTrigger`, `RetriggerSpec`) defers to Phase 3 / Phase 5.

### 4.7 `open_device` factory + `DtolManager` (Phase 2 subset) (M)

**Deliverables:**

- `src/dtollib/__init__.py` exports `open_device` per `design.md` §9.3.
- `open_device(spec, *, backend=None, timeout=10.0, autostart=True)`. Phase 2 always uses `autostart=True` (no callback bridge yet). `autostart=False` is allowed but the session just doesn't start automatically — useful for testing.
- `src/dtollib/manager.py`: `DtolManager.add` / `remove` / `get` / `poll`. `start_synchronized` defers to Phase 5.
- Per-board lock (`design.md` §16.3 conservative stance — start with one lock per HDRVR).
- HDRVR refcount: first `add()` per board → `olDaInitialize`, last `remove()` → `olDaTerminate`.

**Tests:**

- `tests/unit/test_open_device.py`: factory honours `autostart`; backend injection works.
- `tests/unit/test_manager_single_value.py`: add/remove/poll; HDRVR ref-count; concurrent adds against same board share an HDRVR; rejection on subsystem reservation conflict.

### 4.8 Sync facade — Phase 2 subset (M)

**Deliverables:**

- `src/dtollib/sync/session.py`: sync wrapper over `DtolSession` using `SyncPortal` (ported in Phase 0).
- `src/dtollib/sync/daq.py`: `Dtol` class with `Dtol.open_device(spec)` context manager returning sync session.

**Tests:** `tests/unit/test_sync_single_value.py` — round-trip Phase-2 surface; assert behaviour matches async equivalents.

### 4.9 Documentation deliverables for Phase 2

| Doc                         | Content                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `docs/quickstart-async.md`  | TC poll example from `design.md` §7.1                                    |
| `docs/quickstart-sync.md`   | Sync version of TC poll                                                  |
| `docs/task-specs.md`        | `TaskSpec` / `ChannelSpec` reference                                     |
| `docs/channels.md`          | Voltage + TC channel sections (other sensor types: "see Phase 6")        |

### 4.10 Hardware smoke tests (M)

`tests/hardware/test_dt9805_single_value.py`:

- Open DT9805, configure two voltage channels (e.g. `physical_channel=0,1`), poll, assert values are floats in the configured range.
- Open DT9805, configure two K-type TC channels, poll, assert temperatures are in `(min_val_degc, max_val_degc)` *or* the corresponding `sensor_status` is `SENSOR_OPEN` (unplugged TC).
- Same against DT9806.

Gated by `DTOLLIB_ENABLE_HARDWARE_TESTS=1`. Env-var config per `design.md` §22.3.

### 4.11 Phase 2 acceptance criteria

Mirrors `design.md` §26 Phase 2 acceptance, expanded:

1. Quickstart example (`docs/quickstart-async.md`) runs against a DT9805 with K-type TC plugged in and prints temperatures.
2. Same Quickstart, sync version, runs.
3. The same Quickstart **type-checks** on Linux CI against `FakeDtolBackend`.
4. `DtolManager.poll(names=['a','b'])` polls two sessions and returns a mapping of `DeviceResult[DaqReading]`.
5. MULTI_SENSOR ordering test on the fake passes.
6. Sentinel-value test passes: a synthetic SDK-sentinel float in the fake produces `SensorStatus.SENSOR_OPEN` + `NaN` in the reading.
7. Hardware smoke tests green on the bench.
8. All unit tests added in this phase pass on the full CI matrix.

**Output of Phase 2:** A scientist with a DT9805 can replace a QuickDAQ-driven workflow with an async Python script. The streaming / high-rate path is *not yet* available — they're getting on-demand reads only — but the integration with the sibling-library ecosystem (alicatlib / sartoriuslib) works.

---

## 5. Phase 3 — Continuous AI + §12.3.2 callback bridge

**`design.md` cross-ref:** §26 Phase 3, §12.3.2 (the bridge), §13.2 (continuous mode), §14 (recorder design), §15.2 (RawCountsSink), §8.7a (BufferPlan), §8.13 (SubsystemState), §8.14 (BufferState).
**Goal:** Hardware-clocked acquisition end-to-end. A user writes a `TaskSpec` with `data_flow=CONTINUOUS`, opens it with `autostart=False`, and uses `async with record(session) as recording:` to consume `DaqBlock`s from `recording.stream`. All six durable sinks work. `RawCountsSink` writes the `.dt-raw` format and a replay tool reads it back.

This is the most complex phase. The single largest risk area in the library lives in §12.3.2 — the driver-thread → asyncio bridge.

**Bound SDK functions (~25):** Continuous configuration (`olDaSetChannelListSize`, `olDaSetChannelListEntry`, `olDaSetChannelListEntryInhibit`, `olDaSetGainListEntry` list form, `olDaSetClockSource`, `olDaSetClockFrequency`, `olDaGetClockFrequency`, `olDaSetExternalClockDivider`, `olDaSetTrigger`, `olDaSetTriggerThresholdChannel`, `olDaSetTriggerThresholdLevel`, `olDaSetWrapMode`, `olDaSetDmaUsage`), notification (`olDaSetWndHandle` — see §5.5 below; `olDaSetNotificationProcedure` is bound but unused), runtime control (`olDaStart`, `olDaStop`, `olDaAbort`, `olDaIsRunning`, `olDaGetQueueSize`), buffer pool (`olDmCallocBuffer`, `olDmMallocBuffer`, `olDmReAllocBuffer`, `olDmFreeBuffer`, `olDmGetBufferPtr`, `olDmGetBufferSize`, `olDmGetMaxSamples`, `olDmGetValidSamples`, `olDmGetDataWidth`, `olDmGetDataBits`, `olDaPutBuffer`, `olDaGetBuffer`, `olDaFlushBuffers`, `olDmCopyFromBuffer`). `olDaSetStopOnError` is **absent on SDK V7.0.0.7** and is bound only when present (§11.5a).

### 5.1 `tasks/models.py` extensions — `DaqBlock`, `DaqSample` (M)

**Deliverables:**

- `DaqBlock` dataclass per `design.md` §8.10.
- `DaqSample` dataclass per §8.11.
- `block_to_long_rows(block)` helper.
- `BufferState` enum (§8.14).

**Tests:** `tests/unit/test_daqblock.py` — shape constraints, `raw_codes` optional, `cjc_data` optional, `sensor_status` mask shape matches `data` shape.

### 5.2 `tasks/spec.py` extensions — `BufferPlan`, `RawLogging`, `WrapMode`, `QueueStrategy` (M)

Per `design.md` §8.7a. Validation:

- `BufferPlan.buffers >= 3`, default 4.
- `BufferPlan.samples_per_buffer > 0`.
- If `data_flow in {CONTINUOUS, FINITE}` and `buffers is None` → `DtolValidationError`.

`RawLogging` dataclass: path + format flags (`include_metadata: bool = True`, `compression: None = None` for now).

### 5.3 `tasks/triggers.py` — full hierarchy (M)

Per `design.md` §8.8. Phase 3 implements `SoftwareStart`, `ExternalDigitalStart`, `AnalogThresholdStart`, `SyncBusStart`, and `ReferenceTrigger`. `RetriggerSpec` defers to Phase 5 (triggered scan mode).

### 5.4 `backend/_buffer_pool.py` — `BufferPool` (L)

**Deliverables:**

- `RawBuffer` dataclass holding `(hbuf, ndarray_view, state: BufferState, capacity_samples, sample_dtype, valid_samples)`.
- `BufferPool` class managing the Ready/Inprocess/Done lifecycle.

API:

```python
class BufferPool:
    def __init__(self, api: OpenLayersApi, hdass: int, plan: BufferPlan, *,
                 n_channels: int, sample_dtype: np.dtype) -> None: ...
    def allocate(self) -> None: ...       # olDmCallocBuffer × N
    def queue_all(self) -> None: ...      # olDaPutBuffer × N to seed Ready
    def get_done(self) -> RawBuffer | None: ...  # olDaGetBuffer
    def requeue(self, raw: RawBuffer) -> None: ...  # olDaPutBuffer (after copy)
    def flush(self) -> None: ...          # olDaFlushBuffers
    def free_all(self) -> None: ...       # olDmFreeBuffer × N
    @property
    def state_counts(self) -> dict[BufferState, int]: ...  # for diagnostics
```

Invariants:

- `free_all()` refuses to run while any buffer is `INPROCESS` (`design.md` §8.14).
- Use-after-free on a `RELEASED` buffer's ndarray view raises `DtolTaskStateError`.
- `queue_all()` is a one-shot — seeds Ready before commit.

**Tests:**

- `tests/unit/test_buffer_pool.py`: state transitions; allocate / queue / get / requeue / free cycle; double-free detection; use-after-free detection.
- `tests/integration/test_buffer_pool_fake.py`: pool against `FakeDtolBackend` with synthetic block emission.

### 5.5 `backend/_callback_bridge.py` — the §12.3.2 bridge (XL)

This is the single most complex deliverable in the library.

**Deliverable:** `_callback_bridge(backend, hdass, pool, *, error_policy, overflow_policy)` async context manager per `design.md` Appendix C.

**Mechanism — Win32 window-handle + message-pump thread.** Bench-confirmed against SDK V7.0.0.7 (DT9805/DT9806, 2026-05-28): the alternative `olDaSetNotificationProcedure` path silently never fires its callback. Every SDK sample under `%ProgramFiles(x86)%\Data Translation\Win32\SDK\Examples\` uses `olDaSetWndHandle` — including the console example, which creates a hidden `HWND_MESSAGE` window for exactly this purpose. The dtollib API surface (`register_notification` / `unregister_notification`) stays unchanged; only the implementation underneath swaps from `OLNOTIFYPROC` to `WNDPROC` + message pump.

Components:

- **Hidden message-only window** — `CreateWindowExA(HWND_MESSAGE, ...)` per HDASS. The WNDPROC dispatches `OLDA_WM_*` messages to a `queue.SimpleQueue` + `time.monotonic_ns()` per-event. Class registration is cached per process so repeated `register_notification` calls don't leak class atoms.
- **Message-pump thread** — dedicated `threading.Thread` (NOT `anyio.to_thread`, since this thread MUST own the HWND's message queue). Loops on `GetMessageA(&msg, hwnd, 0, 0)` / `DispatchMessageA(&msg)`. Exits when posted a `WM_QUIT` (sentinel from `unregister_notification`).
- **Drainer thread** — long-lived `anyio.to_thread.run_sync` worker. Loops on `queue.get()`; dispatches by `msg_id` (the full `SdkEventKind` enum); for `BUFFER_DONE`, pulls HBUF from pool, copies into ndarray, constructs `DaqBlock`, sends on memory-object-stream, requeues HBUF.
- **WNDPROC + class atom pinning** — strong refs stored on `DataAcqBackend._notification_wrappers[id(hdass)]` (the `WNDCLASS` struct, the WNDPROC closure, the HWND handle, and the pump thread). All dropped only at `unregister_notification`.
- **Sentinel-based shutdown** per `design.md` §12.3.2 shutdown ordering: `olDaStop` → `PostThreadMessage(pump_tid, WM_QUIT)` → join pump thread → `DestroyWindow` → `UnregisterClass` → sentinel-on-queue → drainer joins → free pool.

**Mandatory `olDaConfig` is called TWICE** (bench-verified — see design.md §12.3): once after channel/timing/wrap-mode setup, then again after `olDaSetWndHandle`. Without the second config, buffers stay stuck in INPROCESS and `OLDA_WM_BUFFER_DONE` never fires. `BufferPool.queue_all()` runs between the two configs.

Ordering invariants enforced by both `FakeDtolBackend` and the bridge itself:

| Invariant | Enforced by |
|-----------|-------------|
| Register BEFORE commit | `FakeDtolBackend.commit` rejects if no notification registered (when bridge would expect it) |
| Queue BEFORE commit | `FakeDtolBackend.commit` rejects if Ready queue is empty |
| Commit BEFORE start | `FakeDtolBackend.start` rejects if `_configured` set doesn't include hdass |
| Stop BEFORE unregister | `FakeDtolBackend.unregister_notification` rejects if `is_running(hdass)` |
| Unregister BEFORE sentinel | Bridge code structure; assert in tests via operation log |
| Sentinel BEFORE drain-wait | Bridge code structure |
| Drain-wait BEFORE close | Session `__aexit__` ordering |

**Tests:**

- `tests/unit/test_callback_bridge_ordering.py`: each invariant violated → expected error.
- `tests/unit/test_callback_bridge_drain.py`: synthetic `fire_buffer_done` × N → drainer emits N blocks in order.
- `tests/unit/test_callback_bridge_overrun.py`: synthetic `fire_event(OVERRUN_ERROR)` → behaviour matches `ErrorPolicy` setting.
- `tests/unit/test_callback_bridge_gc.py`: GC pressure between register and first callback fire doesn't break the seam (regression for the wrapper-pinning hazard).
- `tests/unit/test_callback_bridge_shutdown.py`: cancellation during drain → shielded shutdown completes; no orphaned HBUF.

**Risk:** This is where the hardest bugs in the library will live. The mitigation is the FakeDtolBackend's invariant enforcement plus extensive unit testing of the bridge against the fake. Bench validation (§5.10) is the final safety net.

### 5.6 `capi/conversion.py` — vectorised + CJC deinterleave (M)

Now exercised, per `design.md` §11.6:

- `codes_to_volts_vectorised(codes, *, ranges, gains, resolution_bits, encoding)` — pure NumPy.
- `deinterleave_cjc(raw, *, n_channels, n_samples) -> (measurement, cjc)` — only used when `OLSSC_SUP_INTERLEAVED_CJC_IN_STREAM` is in play.
- `detect_thermocouple_sentinel(values, *, tc_type) -> np.ndarray[int8]` — already used by Phase 2's poll().

**Tests:**

- `tests/unit/test_conversion_vectorised.py`: compare against `olDaCodeToVolts` oracle on representative codes (binding test — runs on Windows).
- `tests/unit/test_conversion_deinterleave.py`: synthetic interleaved buffer round-trips correctly.

### 5.7 `streaming/block.py` — `record()` (L)

Per `design.md` §14.1. The recorder is an async context manager that:

1. Validates `session.spec.data_flow in {CONTINUOUS, FINITE}` and `session.spec.buffers is not None`.
2. Runs the prepare → register → queue → commit → start sequence (§12.3.2 startup ordering).
3. Yields `(stream, summary_view)` where `stream` is an `AsyncIterator[DaqBlock]`.
4. On exit: stop → unregister → sentinel → drain-wait → close (shielded).

`record()` ties into the `_callback_bridge` from §5.5 — it's the user-facing facade for the bridge.

**Tests:**

- `tests/unit/test_record.py`: round-trip with `FakeDtolBackend` emitting a known block sequence; assert blocks emerge in order, `AcquisitionSummary` populated correctly.
- `tests/unit/test_record_error_policy.py`: parametrise over RAISE / RETURN / LOG_AND_CONTINUE × OVERRUN injection.
- `tests/unit/test_record_overflow.py`: parametrise over BLOCK / DROP_OLDEST / DROP_NEWEST under simulated consumer slowness.

### 5.8 `streaming/recorder.py` — `record_polled()` (M)

Per `design.md` §12.3.1. Direct port from `nidaqlib/src/nidaqlib/streaming/recorder.py` with type renames.

**Tests:** parallel to `nidaqlib/tests/unit/test_record_polled.py`.

### 5.9 Sinks — six of them (L)

Six durable sinks per `design.md` §15.1:

| Sink            | File                          | Source (port from)                                 |
|-----------------|-------------------------------|----------------------------------------------------|
| `InMemorySink`  | already in Phase 0            | —                                                  |
| `CsvSink`       | `sinks/csv.py`                | `nidaqlib/src/nidaqlib/sinks/csv.py`              |
| `JsonlSink`     | `sinks/jsonl.py`              | `nidaqlib/src/nidaqlib/sinks/jsonl.py`            |
| `SqliteSink`    | `sinks/sqlite.py`             | `nidaqlib/src/nidaqlib/sinks/sqlite.py`           |
| `ParquetSink`   | `sinks/parquet.py`            | `nidaqlib/src/nidaqlib/sinks/parquet.py`          |
| `PostgresSink`  | `sinks/postgres.py`           | `nidaqlib/src/nidaqlib/sinks/postgres.py`         |
| `RawCountsSink` | `sinks/raw_counts.py`         | **NEW** — see §5.10                                |

Each port is mechanical: rename `DaqReading` field references where needed (most fields are name-compatible), add `sensor_status` column propagation per `design.md` §13.1.

**Tests:** lift `nidaqlib/tests/unit/test_sinks.py` and parametrise per sink.

### 5.10 `RawCountsSink` + `.dt-raw` format (L)

Per `design.md` §15.2. The most dtollib-specific deliverable.

**Deliverables:**

- `sinks/raw_counts.py`: `RawCountsSink` class.
- File format v2 (per-chunk framing — `design.md` §15.2).
- Sidecar metadata writer per `design.md` §19.4.
- `dtollib/tools/replay_raw.py`: re-opens `.dt-raw` as iterator of `DaqBlock`s.

Why per-chunk framing matters per `design.md` §15.2 (partial buffers, BUFFER_REUSED, OVERRUN markers, out-of-order seq numbers).

Threading: writes from the **drainer thread**, not the asyncio thread. `np.ndarray.tofile` releases the GIL during I/O (per `design.md` §27 — Risk: RawCountsSink GIL contention). If profiling shows contention, fall back to a background-writer thread; until then, direct-from-drainer is correct.

**Tests:**

- `tests/unit/test_raw_counts_sink.py`: write a known block sequence; reopen; assert data round-trips losslessly.
- `tests/unit/test_raw_counts_replay.py`: replay an `.dt-raw` file → reconstructed `DaqBlock` stream matches original (including `sensor_status`).
- `tests/unit/test_raw_counts_partial_buffer.py`: synthetic final-partial chunk → replay reports correct `valid_samples`.
- `tests/unit/test_raw_counts_overrun_marker.py`: synthetic overrun chunk → replay emits gap marker.

### 5.11 `cli/capture.py` — `dtol-capture` (M)

Per `design.md` §21.2. CLI dispatches by output extension (`.parquet` → `ParquetSink`, `.dt-raw` → `RawCountsSink`).

**Tests:** `tests/unit/test_cli_capture.py` against `FakeDtolBackend` with fast synthetic block emission.

### 5.12 `DtolSession` extensions — Phase 3 methods (M)

Add: `read_block`, `read_inprocess`, `queued_buffer_dones` property. The session.state property now needs to handle the CONTINUOUS-mode transitions (`CONFIGURED_FOR_CONTINUOUS → RUNNING → STOPPING → IO_COMPLETE`).

`read_inprocess` is gated on `CapabilitySet.supports_inprocess_flush()` — raises `DtolCapabilityError` otherwise.

### 5.13 Documentation deliverables for Phase 3

| Doc                         | Content                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `docs/streaming.md`         | `record()` lifecycle, BufferPlan sizing, ErrorPolicy + OverflowPolicy pairings |
| `docs/raw-logging.md`       | `.dt-raw` format spec, `RawCountsSink` usage, replay tool                |
| `docs/timing.md`            | Clock sources, rate quantisation, reading actual rate back               |
| `docs/triggers.md`          | Software / external / threshold / sync bus / reference triggers          |
| `docs/troubleshooting.md`   | Expanded with OVERRUN / UNDERRUN / TRIGGER_ERROR sections                |

### 5.14 60-minute soak test (M, on bench)

Per `design.md` §26 Phase 3 acceptance:

`tests/hardware/test_soak_60_min.py` (opt-in via `DTOLLIB_ENABLE_SLOW_TESTS=1`):

- DT9805, two voltage channels, 1 kHz, `BufferPlan(buffers=4, samples_per_buffer=1000)`, `RawCountsSink` attached.
- Run for 60 minutes.
- Assert: `blocks_dropped == 0`, `overruns_observed == 0`, `.dt-raw` file size matches expected (`60 * 60 * 1000 * 2 * 2` bytes for int16 + headers).
- Replay → reconstructed blocks have monotonic `block_index` 0..N-1 with no gaps.

### 5.15 Deliberate-overrun test (M, on bench)

`tests/hardware/test_overrun.py`:

- DT9805, same rate, but consumer sleeps 200 ms every 10 blocks.
- Run for 5 minutes.
- Assert under `error_policy=RAISE`: recorder raises `DtolBufferOverrunError`.
- Assert under `error_policy=LOG_AND_CONTINUE`: recorder logs WARNING; `AcquisitionSummary.overruns_observed > 0`.
- Assert under `error_policy=RETURN`: error-block emitted; `block.error is not None`; `block.data` is zero-filled.

### 5.16 Phase 3 acceptance criteria

Mirrors `design.md` §26 Phase 3 acceptance, expanded:

1. 60-minute 1 kHz continuous AI on DT9805 drops zero blocks with `BufferPlan(buffers=4)`.
2. `RawCountsSink` round-trips losslessly through the replay tool.
3. Deliberate consumer-pause test triggers `OLDA_WM_OVERRUN_ERROR` and the recorder surfaces it correctly under each `ErrorPolicy`.
4. All five ordering invariants (Register-before-Commit, Queue-before-Commit, Commit-before-Start, Stop-before-Unregister, Unregister-before-Sentinel) are enforced by `FakeDtolBackend` and have passing unit tests.
5. `stop_on_error=False` + `ErrorPolicy.LOG_AND_CONTINUE` pairing test passes (inject OVERRUN, assert SDK keeps producing and recorder logs without raising).
6. Six sinks all accept `DaqBlock` per the §15.1 matrix; `block_to_long_rows` produces correct row counts.
7. `dtol-capture --out run.parquet` and `dtol-capture --out run.dt-raw` both work end-to-end.
8. SubsystemState transitions for CONTINUOUS mode are tested and pass.
9. CJC-interleaved buffer test passes on the fake (real hardware exercise in Phase 6 when TC continuous lands fully).

**Output of Phase 3:** The library is fully usable for the lab's primary high-rate acquisition needs. v0.1 ships.

---

## 6. Phase 4 — Outputs and Digital I/O (DT9806)

**`design.md` cross-ref:** §26 Phase 4, §18 (safety model), §9.1 (`session.write`), §16.3 (locking), §21.4 (CLIs), §8.7a (`BufferPlan`/`WrapMode`).
**Goal:** Bring up the DT9806's D/A (AO), DIN, and DOUT subsystems. Add the safety-gate machinery for writes. Add waveform output (continuous AO) by mirroring the Phase-3 continuous AI pipeline in reverse. Ship the v0.2 CLIs (`dtol-read`, `dtol-info`). End-state: the bench can write a voltage to AO0 and read it back on AI0 (loopback), drive a digital line, and play a continuous waveform for 60 s with zero underruns.

**Bound SDK functions:** `olDaPutSingleValue`, `olDaPutSingleValues`, `olDmCopyToBuffer`, `olDmCopyBuffer`, `olDaSetSynchronousDigitalIOUsage`, `olDaSetDigitalIOListEntry`, `olDaMute`, `olDaUnMute`. (`olDmCopyFromBuffer` is already bound in Phase 3 — reuse, do not redeclare.) If `olDaVoltsToCode` is not already bound by Phase 2, add it here — the single-value AO path needs volts→code on non-`returns_floats` DA subsystems.

### 6.0 Starting state (verified against the tree, 2026-05-28)

Phases 0–3 are landed; much of the Phase-4 scaffolding already exists and only needs wiring. **Do not recreate these:**

| Asset | Location | State |
|-------|----------|-------|
| `DtolConfirmationRequiredError`, `DtolWriteError` | `errors.py` | defined, never raised yet |
| `confirm_start` factory hook | `factory.py` (`del confirm_start`) | dead placeholder, ready to wire |
| `DtolSession.write()` | `tasks/session.py` | `NotImplementedError("…Phase 4")` stub |
| `dtol-read` / `dtol-info` | `cli/read.py`, `cli/info.py` | `stub_main(..., "Phase 4")` |
| `OLSS_DA` / `OLSS_DIN` / `OLSS_DOUT` | `capi/constants.py` | present (1/2/3) |
| `IOType.VOLTAGE_OUT` / `DIGITAL_INPUT` / `DIGITAL_OUTPUT`; `SubsystemType.ANALOG_OUTPUT` etc. | `tasks/models.py` | present |
| `WrapMode.SINGLE` (DAC waveform loop) | `tasks/spec.py` | present |
| `make_fake_dt9806()` with AD+DA+DIN+DOUT+CT subsystems, AO/DIO capability sets | `testing.py` | present |
| CLI conventions (`--json`, hidden `--backend real\|fake`, exit 0/1/2) | `cli/discover.py`, `cli/capture.py` | pattern to follow |

**Net:** Phase 4 is mostly additive plumbing down a settled stack. The one genuinely new hard problem is the **output callback bridge** (§6.4).

Build the workstreams bottom-up (binding → specs → backend → session/CLI → continuous AO) so each layer is green against the layer below before the next is added. Recommended commit order is in §6.10.

### 6.1 SDK binding layer — prototypes, constants, `OpenLayersApi` (M)

Per §1.4 / §1.4a binding + constant gates. **Files:** `capi/prototypes.py`, `capi/constants.py`, `capi/api.py`, `docs/decisions.md`, `scripts/gen_openlayers.py` allowlist.

**Deliverables:**

- `OUTPUT_OLDAAPI_FUNCTIONS` tuple wired into `declare_oldaapi`: `olDaPutSingleValue`, `olDaPutSingleValues`, `olDaSetSynchronousDigitalIOUsage`, `olDaSetDigitalIOListEntry`, `olDaMute`, `olDaUnMute` (+ `olDaVoltsToCode` if absent).
- `WAVEFORM_OLMEM_FUNCTIONS` tuple wired into `declare_olmem`: `olDmCopyToBuffer`, `olDmCopyBuffer`.
- New constants the AO/DIO surface consults — synchronous-DIO-usage flags and any `OLSSC_SUP_SIMULTANEOUS_DA` / programmable-AO capability bits — each transcribed from `OLDADEFS.H` (C) with a line-number citation and bench read-back recorded in the same commit.
- `OpenLayersApi` methods (pure call-shape, `_check`-wrapped, no caching): `put_single_value`, `put_single_values`, `set_synchronous_digital_io_usage`, `set_digital_io_list_entry`, `mute`, `unmute`, `copy_to_buffer`, `copy_buffer` (+ `volts_to_code` if added).

**Binding-gate specifics:** `olDaPutSingleValue` takes a device **code/`LPARAM`**, not volts — confirm width and the `LPARAM`-vs-`LONG` trap element-by-element. A volts/code or width mistake here writes a *wrong voltage to real hardware* with no error. Record one `docs/decisions.md` line per function with the SDK rev.

**Tests:**

- `tests/binding/test_signatures.py` (extend, parametrised): each new function has tuple `argtypes`, `restype == ECODE`, symbol resolves on the stub DLL.
- `tests/binding/test_constants.py` (extend): each new constant matches the SDK header.
- `tests/unit/test_api_all_methods_check.py` already AST-asserts every public API method calls `_check` — the new methods inherit that guard.

**DoD:** every new symbol/constant has a `docs/decisions.md` entry with SDK rev; `gen_openlayers.py --check` green; binding tests pass on the Windows stub-DLL lane.

**Risk:** silent value-encoding bug (above). Mitigation: binding gate + a fake that stores written codes + the §6.9 AO→AI loopback bench check.

### 6.2 New channel specs + serialisation registry (M)

**Files (new):** `channels/analog_output.py`, `channels/digital.py`. **Edit:** `channels/__init__.py`.

> **Updated 2026-05-29 — DIO is port-shaped.** The per-line `DigitalOutputLine` /
> `DigitalInputLine` classes were replaced by `DigitalOutputPort` /
> `DigitalInputPort` (+ `DigitalLine` bit-views) after bench testing proved the
> DT9805/06 expose one 8-bit port per direction, not per-line channels. See
> `docs/decisions.md` (DIO port + bitmask model) and `docs/channels.md`.

**Deliverables** — mirror the frozen/slots/kw_only `ChannelSpec` idiom of `channels/analog_input.py`:

- `AnalogOutputVoltage` per `design.md` §18.3: `min_val=-10.0`, `max_val=10.0` (device electrical range), `safe_min`/`safe_max: float | None = None` (operator safe band, a subset of the device range), `requires_confirm: bool = True`; `kind = "ao_voltage"`; `kind_to_multi_sensor_type() -> IOType.VOLTAGE_OUT`. `__post_init__` rejects a safe band outside `[min_val, max_val]` and `safe_min >= safe_max`.
- `DigitalOutputPort`: `physical_channel` = port index; `width: int | None = None`, `lines: tuple[DigitalLine, ...] = ()`, `safe_value: int | None = None`, `requires_confirm: bool = True`; `kind = "digital_output_port"`.
- `DigitalInputPort`: read-only; `width`, `lines`; `kind = "digital_input_port"`.
- `DigitalLine(bit, name=None, safe_value=None, requires_confirm=None)`: per-bit view into a port (not an SDK channel).
- **Serialisation registry** in `channels/__init__.py`: `channels/__init__.py` currently has **no** `kind → class` registry and **no** `channel_from_dict`. Add both now (needed by `TaskSpec.from_dict` round-trips and the CLIs), registering all five kinds (`ai_voltage`, `thermocouple`, `ao_voltage`, `digital_input`, `digital_output`). This pays a small pre-existing debt here rather than retrofitting later.

**Tests:** `tests/unit/test_channel_specs.py` (extend) — construction, kw-only enforcement, `safe_max > max_val` rejected, `to_dict`/`from_dict` round-trip through the registry for all five kinds.

### 6.3 `session.write` + unified safety gate (M)

Per `design.md` §9.1 + §18. **File:** `tasks/session.py` (replace the stub); **wire:** `factory.open_device(confirm_start=...)`.

```python
async def write(self, values: Mapping[str, float | bool], *, confirm: bool = False) -> None:
    async with self._lock:
        self._require_state_for_write()       # CONFIGURED_* / RUNNING else DtolTaskStateError
        plan = [self._resolve_write(name, v, confirm) for name, v in values.items()]  # validate ALL first
        # branch on caps.supports_simultaneous_da → put_single_values, else loop put_single_value
```

**Unified safety model** (`_resolve_write`) — **decided 2026-05-28, confirm-gate semantics per design §18.1:**

1. Unknown channel name → `DtolValidationError`.
2. Value outside device `[min_val, max_val]` → `DtolValidationError` **always** (electrically impossible; never writable, `confirm` does not override).
3. Value outside `[safe_min, safe_max]` band (when set) **or** channel `requires_confirm=True`, with `confirm is False` → **`DtolConfirmationRequiredError`** naming the channel and band. With `confirm=True` the write proceeds. *(This supersedes the earlier draft of §6.6 #2, which raised `DtolValidationError` for out-of-safe-band; the safe band is a confirmation gate, not a hard clamp — matching `design.md` §18.1.)*
4. **Atomic validation:** every value passes all checks before *any* SDK call — one bad value in a batch ⇒ zero writes. The wrapper never silently clamps.

Wire `confirm_start` so it stops being a dead `del`: `open_device(..., confirm_start=True)` permits an autostart that would otherwise trip a confirm-required output channel.

**Tests:**

- `tests/unit/test_session_write.py`: required-channel without `confirm` raises `DtolConfirmationRequiredError`; with `confirm=True` succeeds; out-of-device-range raises `DtolValidationError` *and the fake op-log shows no `put_*` call*; out-of-safe-band is a confirm gate (raises without confirm, proceeds with); simultaneous-vs-loop dispatch on `supports_simultaneous_da`; atomic validation (one bad value ⇒ zero SDK calls).
- `tests/hardware/test_dt9806_ao.py`: AO0 → AI0 loopback verifies written value (opt-in `DTOLLIB_ENABLE_OUTPUT_TESTS=1`).

### 6.4 Backend protocol + implementations (L)

**Files:** `backend/base.py` (Protocol), `backend/dataacq.py` (real), `backend/fake.py` (fake).

**Deliverables** — add to the `DtolBackend` Protocol and both implementations: `put_single_value`, `put_single_values`, `set_synchronous_digital_io_usage`, `set_digital_io_list_entry`, `mute`, `unmute`, `copy_to_buffer`, `copy_buffer`.

- `dataacq.py`: thin wrappers over the §6.1 `OpenLayersApi` methods, each building `ErrorContext(operation=…, source=…)`. Volts→code via `olDaVoltsToCode` on non-`returns_floats` DA subsystems; direct float path otherwise.
- `fake.py` — **this is where Phase-4 contract enforcement lives.** The fake must: log every call to `self.operations`; reject `put_single_value` on a non-output subsystem (`DtolTaskStateError`); reject writes before `commit`/`start`; honour `supports_simultaneous_da` (reject `put_single_values` when not advertised); **store written values/codes** so loopback-style unit tests can assert them; for continuous AO, track buffer refills so `test_record_ao` can assert "refilled N, zero underrun."

**Tests:** `tests/unit/test_dataacq_backend_output.py` (mocked `OpenLayersApi`); `tests/unit/test_fake_output.py` (every new behaviour + each rejection path). Extend `make_fake_dt9806()` only if new capability fields are required.

### 6.5 Continuous AO (waveform output) (L→XL) — **DEFERRED to a follow-up commit**

> **Status (2026-05-28):** Not yet implemented. The single-value AO/DO/DIN
> surface (§6.1–§6.4, §6.6, §6.7, §6.3) ships first as a complete, tested,
> green increment. Continuous AO is deferred because it requires the full
> output callback bridge (a threaded Win32 message-pump analog of the
> §12.3.2 input bridge) **and** bench read-back on real DT9806 hardware to
> validate underrun/refill semantics (the §1.4 gate). The `FakeDtolBackend`
> `commit` invariant already enforces register-before-commit for *every*
> continuous mode, so even the `WrapMode.SINGLE` path cannot land without
> that bridge. Shipping an unverifiable threaded bridge would violate the
> project's own binding/bench gates. `DtolSession.write` raises a clear
> `DtolTaskStateError` directing continuous AO to `play()`; `play()` itself
> is the deliverable below.

Same callback-bridge shape as Phase 3, in reverse. **Split** the bridge rather than adding a mode flag (`design.md` §12.3.2 hazards apply): the data direction inverts enough — refill vs drain, `UNDERRUN` vs `OVERRUN` — that one code path obscures more than it saves.

**Files (new):** `backend/_output_callback_bridge.py`; **edit:** `streaming/playback.py` (new, the user-facing facade), `tasks/builder.py`, `backend/_buffer_pool.py` (output-fill mode).

**Pipeline:**

- **Seed:** `olDmCopyToBuffer` pre-fills each HBUF with waveform samples *before* commit, then `olDaPutBuffer × N`.
- **`WrapMode` dispatch:** `WrapMode.SINGLE` ⇒ the SDK loops one buffer forever — no refill thread (simplest path; ship first). `WrapMode.MULTIPLE` ⇒ the **refill loop**: on `OLDA_WM_BUFFER_DONE` the drainer pulls the completed HBUF, refills from the waveform source, re-queues (`olDaPutBuffer`). This is the "underrun-prevention loop."
- **Underrun:** `OLDA_WM_UNDERRUN_ERROR` is the analog of `OVERRUN_ERROR`, surfaced per `ErrorPolicy` exactly as the input side surfaces overrun.
- **Shutdown (shielded):** stop (or `mute` → stop) → unregister → sentinel → drain → free pool, reusing the §12.3.2 ordering.

**Waveform-source API — decided 2026-05-28, symmetric with `record()`:** a `play(session, source)` async context manager where `source` is **either** a plain `np.ndarray` (one period, used directly with `WrapMode.SINGLE`) **or** an async iterator / callable `() -> np.ndarray` yielding successive buffer-fulls (used with `WrapMode.MULTIPLE` for arbitrary/streamed waveforms).

**Ordering invariants** (enforced by both the fake and the bridge, mirroring the input table): Fill-before-Queue, Queue-before-Commit, Commit-before-Start, Stop-before-Unregister, Unregister-before-Sentinel.

**Tests:**

- `tests/unit/test_output_buffer_pool.py`: fill/queue/refill/free cycle; refuse `free_all()` while any buffer is `INPROCESS`.
- `tests/unit/test_record_ao.py`: synthetic sine `source` → fake fires N `BUFFER_DONE` → drainer refills N → zero UNDERRUN; refilled sample sequences match `source`.
- `tests/unit/test_record_ao_underrun.py`: inject `UNDERRUN_ERROR`, parametrised over `ErrorPolicy` RAISE / RETURN / LOG_AND_CONTINUE.
- `tests/unit/test_output_bridge_ordering.py`: each invariant violation → expected error.
- `tests/hardware/test_dt9806_waveform.py`: loop-back AO → AI on DT9806; recovered waveform matches generated; 60 s run, zero UNDERRUN.

**Risk:** the single hardest deliverable of the phase (same class as the Phase-3 input bridge — WNDPROC pinning, GC, shielded shutdown). Mitigation: split the bridge, port the invariant table into the fake, and ship the `WrapMode.SINGLE` (no-refill) path first to de-risk before the refill loop.

### 6.6 TaskBuilder output dispatch (M)

**File:** `tasks/builder.py`. Add an output path parallel to `configure_single_value`, dispatching on channel kind:

- AO single-value: per-channel range/gain, `set_stop_on_error`, `commit`.
- AO continuous: channel-list + clock + `WrapMode` (SINGLE/MULTIPLE) + buffer seeding hand-off to §6.5.
- DIN/DOUT: `set_synchronous_digital_io_usage` + per-line `set_digital_io_list_entry`.

Assert the channel kind matches the HDASS subsystem type (AO channel on an `OLSS_DA` HDASS, etc.) and raise `DtolValidationError` *before* any setter. Reuse the MULTI_SENSOR ordering-guard pattern.

**Tests:** `tests/unit/test_task_builder_output.py` — assert the exact call sequence on the fake for AO single-value, AO continuous, and DOUT; mismatched-subsystem rejection.

### 6.7 `cli/read.py`, `cli/info.py` (M)

Per `design.md` §21.4. Replace the `stub_main` bodies, following the `cli/discover.py` + `cli/capture.py` conventions: `argparse`, hidden `--backend real|fake` for tests, `--json`, exit codes 0 (ok) / 1 (SDK/enum failure) / 2 (bad invocation).

- `dtol-read --board DT9805(00) --channel 0 --range -10,10 [--json]` — one-shot scalar read via the existing Phase-2 single-value path. **Depends only on existing AI + the §6.2 registry** — can ship early, independent of §6.3/§6.5.
- `dtol-info --board DT9805(00) [--json]` — full per-board `CapabilitySet` dump + per-subsystem channel-list rendering over existing Phase-1 capability data.

**Tests:** `tests/unit/test_cli_read.py`, `tests/unit/test_cli_info.py` against `make_fake_dt9806()` via `--backend fake`; assert `--json` parses.

### 6.8 Documentation deliverables for Phase 4

| Doc                          | Content                                                                                  |
|------------------------------|------------------------------------------------------------------------------------------|
| `docs/safety.md` (new)       | `requires_confirm` rules, `safe_min`/`safe_max` confirm-gate semantics, atomic-validation guarantee, `write()` examples |
| `docs/channels.md` (extend)  | AO / DIN / DOUT sections                                                                  |
| `docs/waveform-output.md` (new) | `play()` lifecycle, `WrapMode.SINGLE` vs `MULTIPLE`, underrun/buffer sizing, `ErrorPolicy` pairings |
| `docs/decisions.md` (extend) | §6.1 prototype/constant verifications                                                     |
| `CHANGELOG.md`               | v0.2 entries                                                                              |

### 6.9 Hardware bench validation (M, opt-in)

Gated by `DTOLLIB_ENABLE_OUTPUT_TESTS=1` + the `hardware_output` marker (`design.md` §22.3). Depends on **Open Question 7 — loopback wiring** (does the bench have AO0→AI0 / DOUT→DIN jumpers?).

- `tests/hardware/test_dt9806_ao.py`: AO0 write with `confirm=True` → read AI0 within tolerance; without `confirm` raises.
- `tests/hardware/test_dt9806_do.py`: DOUT line → DIN read-back.
- `tests/hardware/test_dt9806_waveform.py`: sine on AO0, captured on AI0, recovered waveform matches; 60 s run, zero UNDERRUN.

### 6.10 Suggested commit sequence

Each row is an independently reviewable, CI-green commit. **Steps 1–6 + 8a
are landed (2026-05-28); step 7 + bench validation remain.**

1. ✅ §6.1 binding (prototypes/api + binding tests + `decisions.md`).
2. ✅ §6.2 channel specs + serialisation registry.
3. ✅ §6.4 backend protocol + dataacq + fake (the contract layer).
4. ✅ §6.7 `dtol-info` + `dtol-read`.
5. ✅ §6.3 `write()` + safety gate + factory `confirm_start` wiring.
6. ✅ §6.6 builder output dispatch (single-value AO/DO via output-aware `add_channel`).
7. ⏳ §6.5 continuous AO — output callback bridge + `play()` (`WrapMode.SINGLE`
   first, then the `MULTIPLE` refill loop). **Deferred** — see §6.5 note.
8. ✅ §6.8 docs (safety.md, channels.md) + CHANGELOG for the shipped surface;
   ⏳ §6.9 bench validation (needs hardware) remains.

**Landed in this increment:** `olDaPutSingleValue`/`olDaPutSingleValues`/
`olDaMute`/`olDaUnMute`/`olDaSetSynchronousDigitalIOUsage`/
`olDaSetDigitalIOListEntry`/`olDmCopyToBuffer`/`olDmCopyBuffer` bound +
`OpenLayersApi` methods; `AnalogOutputVoltage`/`DigitalInputPort`/
`DigitalOutputPort` (+ `DigitalLine`) + `channel_from_dict` registry; backend Protocol +
`DataAcqBackend` + `FakeDtolBackend` output methods; `DtolSession.write` with
the confirm-gate safety model; `open_device(confirm_start=...)` wiring;
output-aware `TaskBuilder` path; `dtol-read` / `dtol-info` CLIs. All green on
`mypy --strict` + `pyright` + `ruff` + the unit suite.

### 6.11 Phase 4 acceptance criteria

Mirrors `design.md` §26 Phase 4. Concrete:

1. AO write with `confirm=True` succeeds; without it on a `requires_confirm` channel raises `DtolConfirmationRequiredError`.
2. AO write outside device `[min_val, max_val]` raises `DtolValidationError` *before* any SDK call (verified via the fake op-log).
3. AO write outside `[safe_min, safe_max]` is a **confirm gate** — raises `DtolConfirmationRequiredError` without `confirm`, proceeds with `confirm=True` (decided 2026-05-28; supersedes the prior `DtolValidationError` wording).
4. DO write to a `requires_confirm` line follows the same gate.
5. Atomic validation: one bad value in a batch ⇒ zero SDK writes.
6. Continuous AO waveform path is software-complete and runs zero-UNDERRUN against the fake (`test_record_ao`). **The bench 60 s zero-UNDERRUN target is withdrawn** (B1 in `plan-hardware-functional.md`): the DT9806 D/A is single-value only, so `play()` fails loud with `DtolCapabilityError`. Retargeted to a future continuous-DAC board.
7. AO loopback on DT9806 recovers the written value within tolerance (depends on Open Question 7 — loopback wiring).
8. `dtol-read` and `dtol-info` print expected text + parseable `--json` against both DT9805 and DT9806.
9. All five output-bridge ordering invariants enforced by the fake with passing tests.
10. `mypy --strict` + `pyright --strict` + `ruff check`/`format --check` + coverage gate (90 % stmt / 80 % branch) all green.

---

## 7. Phase 5 — Counter/Timer, Tachometer, Simultaneous Start

> **Implementation status (2026-05-28):** the software stack landed and is
> green on the fake-backend CI surface — channel specs + enums, the
> `COUNTER_OLDAAPI_FUNCTIONS` prototypes + `OpenLayersApi` methods, the `DtolBackend` /
> `DataAcqBackend` / `FakeDtolBackend` counter+sync surface, the
> `TaskBuilder.configure_counter` path + triggered-scan wiring,
> `DtolSession.read_events` / `measure_frequency`, and
> `DtolManager.start_synchronized`. Unit tests cover specs, fake contracts
> (C/T-mode-first ordering, RUNNING-before-read, SS-list ordering), builder
> call sequences, session reads, dataacq passthroughs, and manager
> coordination; `mypy --strict` + `pyright` + `ruff` + the suite all pass.
> **Both constant/hardware gates are now resolved (bench 2026-05-28, SDK
> V7.0.0.7 — docs/decisions.md):** OQ-5a — the `OL_CTMODE_*` / `OL_GATE_*` /
> `OL_PLS_*` / `OL_EDGE_*` / cascade values are transcribed from `OLDADEFS.H`
> and read back on the live C/T; OQ-5b — the DT9805/06 expose no
> quadrature/tachometer subsystem and no `CTMODE_MEASURE`, so those modes are
> capability-gated off (`DtolCapabilityError`). Remaining bench work is the
> end-to-end *acceptance* on the bench: **event-count** (feed a known TTL pulse
> burst to the C/T input → `CounterEdgeCount` + `read_events()`, assert count)
> and **pulse-train output** (`PulseTrainOutput` → capture on an AI loopback or
> scope, assert frequency) — both need a signal generator; and
> `start_synchronized` AI+C/T alignment within one sample period (note: the C/T
> subsystem reports `OLSSC_SUP_SIMULTANEOUS_START` = 0 on these boards, so this
> needs investigation first). The continuous-AI `record()` path these build on
> is bench-verified (WS-A0, see decisions.md).

**`design.md` cross-ref:** §26 Phase 5, §16.1 (`start_synchronized`).
**Goal:** Multi-subsystem coordination on the DT9806. Bring up the C/T subsystem, the quadrature decoder, the tachometer (as a first-class `OLSS_TACH`), and the SDK's simultaneous-start primitives. The "two HDASSes start within one sample period" alignment is the *software* contract; on owned hardware it is achievable only where the subsystem reports `SUP_SIMULTANEOUS_START` (the C/T reports 0 — B5 in `plan-hardware-functional.md`). Quadrature/tachometer are software-complete but gated off on owned hardware (no `OLSS_QUAD`/`OLSS_TACH` — B4).

**Bound SDK functions:** `olDaSetCTMode`, `olDaSetClockSource`, `olDaSetClockFrequency` (generic — no `olDaSetCTClock*` export exists), `olDaSetGateType`, `olDaSetPulseType`, `olDaSetPulseWidth`, `olDaSetMeasureStartEdge`, `olDaSetMeasureStopEdge`, `olDaSetCascadeMode`, `olDaReadEvents`, `olDaMeasureFrequency`, `olDaSetTriggeredScanUsage`, `olDaSetMultiscanCount`, `olDaSetRetriggerMode`, `olDaSetRetrigger`, `olDaSetRetriggerFrequency`, `olDaGetSSList`, `olDaPutDassToSSList`, `olDaSimultaneousPrestart`, `olDaSimultaneousStart`, `olDaReleaseSSList`.

### 7.1 Counter/timer channel specs (M)

- `channels/counter_input.py`: `CounterEdgeCount`, `CounterFrequency`, `CounterEdgeToEdge`, `QuadratureDecoder`, `Tachometer`.
- `channels/counter_output.py`: `PulseTrainOutput`, `OneShotOutput`, `RepetitiveOneShotOutput`.

Each spec carries its CT mode (`OLSS_CT_RATE_GENERATE`, `OLSS_CT_ONE_SHOT`, ...) as an enum. The builder dispatches on type.

### 7.2 `SubsystemType.TACHOMETER` as first-class (S)

Per `design.md` §8.12. `Tachometer` channels route through `OLSS_TACH`, not `OLSS_CT`. The builder asserts the channel's subsystem-type matches the spec's.

### 7.3 `RetriggerSpec` + triggered scan mode (M)

Per `design.md` §8.8. Default to `OL_RETRIG_EXTRA` when both are supported (per SDK doc recommendation).

### 7.4 `DtolManager.start_synchronized` (L)

Per `design.md` §16.2:

```python
async def start_synchronized(self, names: Sequence[str]) -> None:
    # 1. olDaGetSSList
    # 2. for name in names: olDaPutDassToSSList
    # 3. olDaSimultaneousPreStart
    # 4. olDaSimultaneousStart
    # 5. olDaReleaseSSList
```

Each step wraps the API in the standard error-context shape.

**Tests:** `tests/unit/test_manager_synchronized.py` (mirrors `nidaqlib/tests/unit/test_manager_synchronized.py`); `tests/hardware/test_synchronized.py` (DT9806 AI + C/T).

### 7.5 Documentation deliverables for Phase 5

| Doc                         | Content                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `docs/counter-timer.md`     | Counter input/output, quadrature, tachometer                             |
| `docs/synchronized.md`      | `start_synchronized` semantics + which subsystems can coordinate        |

### 7.6 Phase 5 acceptance criteria

1. DT9806 with AI + C/T running concurrently — `start_synchronized` puts the first samples within one sample-period of each other (measured via Phase-3 RawCountsSink alignment).
2. Quadrature decoder reads correct position from a manually-rotated encoder.
3. Tachometer reads correct frequency from a known signal generator input.
4. Retrigger mode (EXTRA) produces jitter-free scans (measured via histogram of inter-scan intervals).

---

## 8. Phase 6 — Full Multi-Sensor Coverage

**`design.md` cross-ref:** §26 Phase 6, §13.2 (continuous TC).
**Status:** Track A software **landed 2026-05-29** (continuous TC linearisation); Track A §A.1 polynomials blocked on the NIST outage; Track A §A.4 bench-pending. **Track B software landed 2026-05-28** (specs, bindings, dispatch, capability gate, TEDS, conversions — all CI-verifiable on the fake; real-sensor verification deferred until a multi-sensor DT module is acquired). The A.3 `is_linearised` follow-up is also done (threaded through `block_to_long_rows` / sink rows / `replay_raw`).

### 8.0 Headline correction (supersedes the pre-bench plan)

`design.md` §8.4/§8.6 and the original §8 were written before the 2026-05-28 bench session and assumed facts the hardware disproved. Three corrections drive the whole phase (full detail in `docs/decisions.md` 2026-05-28/29 entries and the project memory):

- The owned **DT9805/06 report `supports_multisensor=False`** and reject every multi-sensor setter with ECODE 36. RTD/thermistor/strain/bridge/IEPE target *intelligent* modules (DT9828/9829/9837) the lab does **not** own.
- **There is no `OLSSC_SUP_LINEARIZE_TC`** in this SDK. The app-side TC path is gated on `supports_thermocouples and not returns_floats`.
- Application-side TC linearisation is **already required and shipped** for single-value (Open Question 9 resolved **YES** by the bench). The gap Phase 6 closes is the **continuous/block path**.

Consequently Phase 6 splits into two tracks:

- **Track A — bench-achievable (the real value).** Continuous/block-path TC linearisation + the NIST ITS-90 polynomial set. Validatable on the DT9806 K-type rig.
- **Track B — spec library, hardware-deferred (user decision 2026-05-28).** RTD/thermistor/strain/bridge/IEPE/current/resistance specs + bindings + TEDS, fully typed/fake-tested/capability-gated; acceptance bar is "fake-proven configure path + clean `DtolCapabilityError` on owned gear", with real-sensor verification deferred until a multi-sensor DT module is acquired.

---

### Track A — continuous TC linearisation

#### 8.A1 Complete the NIST ITS-90 polynomial set (M) — **BLOCKED**

Add inverse + forward polynomials for **T, E, R, S, B, N** to `utils.py` (K & J already ship and are bench-validated). **Blocked 2026-05-29:** the NIST ITS-90 service (SRD 60) returns HTTP 503 on every endpoint, the Omega vendor PDF is 410 Gone, and the `thermocouples_reference` package downloads from the same down NIST file. Not transcribing from unverified secondary sources (§1.4a: "named but unverified is worse than missing"); the bench has only K-type TCs so this blocks no hardware acceptance. **Resume when NIST is reachable.** Per-type reference-vector tests in `test_utils.py` land with the coefficients.

#### 8.A2 Block-path TC linearisation in the drainer (L) — **DONE 2026-05-29**

Continuous `DaqBlock.data` now carries engineering units (volts / °C) instead of raw codes. Delivered:

- `capi/conversion.py::BlockConversion` (plan) + `linearise_block()` (vectorised code→volts→°C kernel with CJC correction, open-circuit + envelope masking). Unit-tested in `tests/unit/test_block_linearisation.py`.
- **CJC is sourced from a scan-list row (ch0, gain 1), not the interleaved stream** (`olDaSetReturnCjcTemperatureInStream` returns ECODE 36 on these boards). `deinterleave_cjc` retained for true intelligent modules.
- `DtolBackend.get_scaling()` (base + dataacq + fake); `BridgeConfig.conversion` + drainer branch in `_callback_bridge.py`; `streaming/block.py::_build_conversion_plan` builds the plan after `olDaConfig` from the `CapabilitySet` + per-channel gains/TC types.
- Guards: `record()` **fails loud** (`DtolTaskStateError`) if a continuous TC task omits its CJC channel, and rejects a TC sitting on its own CJC channel. End-to-end fake test: `tests/unit/test_record_tc_linearisation.py`.
- Per-sample NIST inverse is a Python loop (fine at bench scan rates; vectorisation is a Phase 7 perf item).

#### 8.A3 `DaqBlock` engineering-units contract (S) — **DONE (follow-up complete 2026-05-28)**

`DaqBlock.is_linearised: bool` distinguishes engineering-units data from the raw-codes fallback so sinks/replay don't guess. **Follow-up now done:** the flag is threaded through `DaqSample` (`block_to_long_rows` propagates it; `DaqSample.to_dict` emits it, so CSV/JSONL/Postgres rows are self-describing) and `replay_raw` sets it explicitly `False` (a `.dt-raw` file only ever stores raw codes). The SQLite block-summary row already carried it. Tests in `test_daqblock.py`.

#### 8.A4 Bench validation — continuous TC vs reference (M) — **test written, bench-pending**

`tests/hardware/test_continuous_tc.py` (gated by `DTOLLIB_ENABLE_HARDWARE_TESTS=1`): DT9806, K-type TCs on ch4/ch6 + CJC ch0, asserts `is_linearised` °C blocks in a plausible band, open TC → `SENSOR_OPEN`; reference-thermometer tolerance check enabled via `DTOLLIB_TC_REFERENCE_C`. Needs the bench rig to run.

#### 8.A5 Single-value AI code→volts (S) — **DONE 2026-05-29**

The single-value counterpart to 8.A2: `DtolSession.poll()` on a raw-code subsystem (`returns_floats` False — every owned DT9805/06) previously surfaced the raw 16-bit ADC code for an `AnalogInputVoltage` channel. The int branch of `_read_all_channels` now converts the code to volts at the gain it was read at (1.0 for the simultaneous batch read, `channel.gain` per-channel), matching the float and thermocouple paths. `code_to_volts` is a pure cached conversion on the real backend, so the per-channel call is cheap — the stale "Phase 3 vectorised converter" deferral is gone. Conversion is shared with the application-side TC path via the new `DtolSession._code_to_volts` helper. Non-analog int channels (raw port/counter codes) keep their integer. Unit coverage: `TestSingleValueVoltagePoll` in `test_session_single_value.py`. Closed the implementation handoff (code side; the handoff doc has been removed). Bench-confirmed via the public `loopback-ao` (AO0=+2.5 V → AI1 +2.313 V, see bench-dio-ao.md "Fix A verified on hardware"); the gated `AO0→AI0` pytest re-verify is still pending an AI0 wire + offset reduction.

---

### Track B — multi-sensor spec library (hardware-deferred) — **software landed 2026-05-28**

**Bound SDK functions:** `olDaSetRtdType`, `olDaSetRtdR0`, `olDaSetRtdA/B/C`, `olDaSetThermistorA/B/C`, `olDaSetExcitationCurrentSource`, `olDaSetExcitationCurrentValue`, `olDaSetCouplingType`, `olDaSetStrainExcitationVoltageSource`, `olDaSetStrainExcitationVoltage`, `olDaSetStrainShuntResistor`, `olDaSetStrainBridgeConfiguration`, `olDaSetBridgeConfiguration`, `olDaVoltsToStrain`, `olDaVoltsToBridgeBasedSensor`, `olDaReadBridgeSensorHardwareTeds`, `olDaReadBridgeSensorVirtualTeds`, `olDaReadStrainGageHardwareTeds`, `olDaReadStrainGageVirtualTeds`. All header-verified against `OLDAAPI.H`/`OLDADEFS.H` + symbol-confirmed on the live `oldaapi64.dll` (see decisions.md 2026-05-28). **`olDaSetIEPE` and `olDaSetTransducerType` do NOT exist on this SDK** — dropped per "bind when present"; IEPE is configured via `olDaSetCouplingType(AC)` + the excitation-current setters. Setters tolerate ECODE 36 on owned hardware via the established `try/except DtolError if ecode != OL_NOT_SUPPORTED: raise` pattern.

#### 8.B1 Sensor channel specs + enums (L) — **DONE**

`channels/analog_input.py`: `RtdInput`, `ThermistorInput`, `StrainInput`, `BridgeInput`, `IepeInput`, `CurrentInput`, `ResistanceInput`, each `kind_to_multi_sensor_type()` → the matching `IOType`. New enums: `RtdType`, `ExcitationSource`, `StrainExcitationSource`, `StrainGageConfiguration`, `BridgeConfiguration` (`TransducerType` dropped — no SDK setter). `__post_init__` rejects clearly-wrong combos (`IepeInput(coupling=DC)`, custom-RTD without coefficients, non-positive gage factor, …). Exported from the package; registered in `_CHANNEL_KINDS`. Tests: `test_channel_specs.py::TestMultiSensorSpecs` + round-trip cases.

#### 8.B2 SDK binding (M) — honors §1.4/§1.4a gates — **DONE**

`MULTI_SENSOR_OLDAAPI_FUNCTIONS` (+ `TEDS_OLDAAPI_FUNCTIONS`) in `capi/prototypes.py`, verified against `OLDAAPI.H`; surfaced on `OpenLayersApi` mirroring `set_thermocouple_type`. New SDK constants (`OL_RTD_TYPE_*`, `OL_COUPLING_*`, `OL_EXCITATION_CURRENT_SRC_*`, `OL_STRAIN_*`, `OL_BRIDGE_*`) in `capi/constants.py`. Recorded in `decisions.md`; `gen_openlayers.py` allowlist now loads every phase (was Phase 1 only). Tests: `test_capi_prototypes.py`, `test_capi_api_check_invariant.py`.

#### 8.B3 Backend Protocol + DataAcqBackend dispatch + Fake (L) — **DONE**

`dataacq.py::add_channel` dispatches each new spec through `_configure_multi_sensor` (per-type setters, ec=36 tolerated via `_tolerate_unsupported`); the stale `_IO_TYPE_TO_OLSS_MULTI_SENSOR` table is corrected to the verified `IOTYPE_*` ordinals. `fake.py` records `multi_sensor_types`/`multi_sensor_specs` and keeps the MULTI_SENSOR ordering guard; `testing.py::make_fake_multisensor()` reports `supports_multisensor=True`. Tests: `test_fake_multisensor.py`, `test_dataacq_backend_multisensor.py`, `test_task_builder_multisensor.py`.

#### 8.B4 Builder capability gate (M) — **DONE**

`TaskBuilder._require_io_type_supported(channel, capabilities)` (mirrors `_require_counter_mode_supported`) raises `DtolCapabilityError` at configure time for any multi-sensor-only spec on a `supports_multisensor=False` subsystem — owned DT9805/06 fail cleanly instead of leaking a raw ec=36. Voltage + thermocouple specs are exempt. Wired into both `configure_single_value` and `configure_continuous`.

#### 8.B5 `teds.py` — TEDS read helpers (M) — **DONE**

`STRAIN_GAGE_TEDS`/`BRIDGE_SENSOR_TEDS` ctypes structs in `capi/types.py` (verbatim from `TedsApi.h`); four `OpenLayersApi` readers; backend passthroughs; fake scriptable payloads. `teds.py` exposes `StrainGageTeds`/`BridgeSensorTeds` dataclasses + `read_{strain_gage,bridge_sensor}_teds` (capability-gated) + `_virtual_teds` (un-gated). Tests: `test_teds.py`.

#### 8.B6 `volts_to_strain` / `volts_to_bridge` conversion (S) — **DONE**

`olDaVoltsToStrain`/`olDaVoltsToBridgeBasedSensor` surfaced on `OpenLayersApi` + backend; `strain.py` exposes `strain_from_volts` / `bridge_value_from_volts` that read the gage/bridge parameters off the channel spec. Pure-Python rosette transforms remain in `utils.py`. Tests: `test_strain_conversion.py`. (Live-DLL ABI confirmed for the structs + conversions; real-sensor accuracy deferred.)

---

### 8.5 Documentation deliverables for Phase 6

| Doc                         | Content                                                                 |
|-----------------------------|-------------------------------------------------------------------------|
| `docs/channels.md`          | Full sensor coverage — each marked "requires a multi-sensor DT module; not supported on DT9805/06" with a capability-check snippet |
| `docs/teds.md`              | TEDS read helpers                                                        |
| `docs/decisions.md`         | §A.2 design entry (done); per-binding verifications (Track B)            |

### 8.6 Phase 6 acceptance criteria (revised for bench reality)

Track A:

1. **[done, CI]** Continuous TC `record()` yields linearised °C `DaqBlock`s on the fake; open-TC → `SENSOR_OPEN` + NaN; `is_linearised` set.
2. **[bench-pending]** Continuous TC run on the DT9806 tracks a reference thermometer within K-type tolerance (`test_continuous_tc.py`).
3. **[blocked]** All 8 NIST types linearise with reference-vector tests green (T/E/R/S/B/N pending NIST access).

Track B (hardware-deferred):

4. **[done, CI]** All seven specs construct/validate/round-trip; builder + fake exercise the full configure path with correct MULTI_SENSOR ordering (`test_task_builder_multisensor.py`, `test_fake_multisensor.py`, `test_dataacq_backend_multisensor.py`).
5. **[done, CI; bench-pending on owned gear]** Committing any multi-sensor task on a `supports_multisensor=False` subsystem raises `DtolCapabilityError` cleanly — proven on the fake DT9805; will re-confirm on the physical board next bench session.
6. **[done, CI]** TEDS read returns typed metadata on the fake; hardware read raises `DtolCapabilityError` cleanly on a non-multisensor board (`test_teds.py`).
7. **[conditional]** RTD/IEPE/strain + real TEDS verified against sensors — only if a multi-sensor DT module (DT9828/9829/9837) is acquired.

---

## 9. Phase 7 — Hardening / v1.0 Beta

**`design.md` cross-ref:** §26 Phase 7.
**Goal:** Long-tail edge cases, performance work, public API freeze.

### 9.1 Pre-trigger / about-trigger data flow modes (M, conditional)

Only if owned hardware supports them — flagged "Legacy Devices" in the manual. Adds `DataFlow.CONTINUOUS_PRETRIGGER` and `DataFlow.CONTINUOUS_ABOUT_TRIGGER` to the dispatch table.

### 9.2 Multi-board simultaneous start via Sync Bus (M)

`olDaSetSyncMode` + a second board on the same Sync Bus. Distinct from Phase 5's `start_synchronized` which is single-board; here we coordinate two DT devices.

### 9.3 `olDaAutoCalibrate` with destructive gate (S)

Treated as destructive — gated behind `DTOLLIB_ENABLE_DESTRUCTIVE_TESTS=1` in tests, behind `confirm=True` at the API. Same shape as the Phase-4 safety gates.

### 9.4 Long-duration stress (L, on bench)

24-hour continuous AI runs with `RawCountsSink`. Assert:

- Zero drift in `AcquisitionSummary.blocks_dropped`.
- Zero growth in `state_counts[BufferState.RELEASED]` (no slow buffer leak).
- File-system stability (RawCountsSink file rotation if a single file exceeds 4 GB).
- Memory stability via `tracemalloc` snapshots.

### 9.5 Lock relaxation experiments (M)

Per `design.md` §16.3. Measure under realistic load. If profiling shows lock contention on DT9806 AI + C/T concurrently, relax to per-HDASS locks for that subsystem pair. Document the bench experiment as a `docs/decisions.md` entry.

### 9.6 SPSC variant for the §12.3.2 path (M, conditional)

Per `design.md` §26 Phase 7. If profiling on the §12.3.2 path shows `queue.SimpleQueue` contention (unlikely at < 100 kS/s but plausible at high rates), swap for a lock-free single-producer/single-consumer ring buffer. Measure before swapping.

### 9.7 Public API freeze for v1.0 beta (S)

- Lock the public `__init__.py` surface.
- Tag a release candidate.
- Beta-test against a wider lab user group.
- Document any deprecations as `# DEPRECATED in v1.0` comments + warnings.

### 9.8 Phase 7 acceptance criteria

1. 24-hour soak test passes.
2. Sync Bus dual-board coordination works (depends on Open Question 11 if collections become relevant).
3. Public API freeze documented; CHANGELOG.md has a v1.0.0 entry.
4. Beta release published with at least one external user successfully running an experiment.

---

## 10. Future / Not Committed (post-v1.0)

Per `design.md` §26 "Future / not committed":

- **`DtolCollection`** for VIBbox-style chassis (Open Question 11). Adds `find_collections()` + master/slave model. Deferred until a real collection-style rig arrives in the lab.
- **TimescaleDB sink** for high-rate Postgres logging.
- **HDF5 sink** as an alternative to `.dt-raw`.
- **Cross-instrument synchronisation primitives** (DT external trigger shared with NI on a common back-plane).
- **Web dashboard** (probably belongs in a separate package).

Each of these is a self-contained add-on against a stable v1.0 public API. They do not block v1.0.

---

## 11. Cross-Phase Concerns

### 11.1 Documentation cadence

Every phase produces docs **before** the phase is declared done. The `docs/` paths in each phase section above are the minimum. The right rhythm:

1. Phase enters implementation.
2. Module-level docstrings get written *with* the code.
3. Quickstart / topic docs get written when the phase is functionally complete but before acceptance gate.
4. Reference docs (`docs/api/`) are auto-generated by mkdocstrings on `docs.yml` workflow.

### 11.2 Versioning + release cadence

- Phase 0 → no release (`0.0.0` for testing only).
- Phase 1 → `0.0.1` alpha (discovery + diag CLI). Internal release.
- Phase 2 → `0.1.0` alpha (single-value works). First PyPI release.
- Phase 3 → `0.1.0` final / `0.2.0` (continuous works). Major user-facing release.
- Phase 4 → `0.3.0` (outputs work). DT9806-complete-input + write.
- Phase 5 → `0.4.0` (C/T + synchronised start).
- Phase 6 → `0.5.0` (multi-sensor complete).
- Phase 7 → `1.0.0-beta1` → `1.0.0`.

Use `hatch-vcs` tag-based versioning. Each release ships a CHANGELOG.md entry.

### 11.3 Decision log

`docs/decisions.md` is the single canonical file for:

- Type alias verifications (Phase 1+).
- Prototype signature verifications (per phase).
- SDK version cross-checks.
- Open Question resolutions.
- Per-phase lock-relaxation experiments (Phase 7).
- Any "we considered X, chose Y because Z" decision.

### 11.4 Open Question resolution map

`design.md` §31 has 11 Open Questions. Each should be resolved before its dependent phase begins:

| Q  | Topic                          | Resolution due before |
|----|--------------------------------|------------------------|
| 1  | Package name                   | Phase 0 (decides PyPI publish target) |
| 2  | Bitness for first bench        | Phase 1 (decides loader default) |
| 3  | SDK headers as repo fixtures   | Phase 1 (decides CI diff-check scope) |
| 4  | Actual board names             | Phase 1 (decides smoke-test env-var values) |
| 5  | Primary v0.1 use case          | Phase 2 (decides quickstart example ordering) |
| 6  | DT9806 AO in v0.1?             | Phase 3 (decides whether to slip Phase 4 forward) |
| 7  | Loopback wiring for output tests | Phase 4 (decides closed-loop vs open-loop test design) |
| 8  | `.dt-raw` replay ergonomics    | Phase 3 (decides which replay shapes ship) |
| 9  | Application-side TC linearisation | Phase 6 (decides whether to implement ITS-90 path) |
| 10 | TimescaleDB / HDF5 sinks       | post-v1.0 |
| 11 | `DtolCollection` priority      | post-v1.0 |

Each resolution lands as a `docs/decisions.md` entry, plus a one-line edit to `design.md` if the resolution diverges from the parenthesised default.

### 11.5 Sibling library mirroring

This package is the fifth in the `*lib` family. The discipline is:

- New shared code lands in the most recent sibling (probably `dtollib` itself) and back-ports happen on a deliberate cadence — they're not automatic.
- Cross-library changes that affect ecosystem joins (e.g. `DaqReading` field renames) are coordinated explicitly via a "ecosystem sync" PR touching all five repos.
- The `t_mono_ns` join contract is sacred. Any change requires a major version bump across all five.

---

## 12. Per-Phase Deliverables Matrix

Compact reference, one row per phase. Columns: SDK functions newly bound, public-API additions, sinks/CLIs newly enabled, acceptance hardware.

| Phase | New SDK fns | New public API                                                              | Sinks/CLIs                       | Bench hardware |
|-------|-------------|------------------------------------------------------------------------------|----------------------------------|----------------|
| 0     | 0           | `DtolError`, `DtolConfig`, ported infrastructure                            | InMemorySink only; no CLIs       | none           |
| 1     | 15          | `find_devices`, `CapabilitySet`, `dtollib.utils`                            | `dtol-discover`, `dtol-diag`     | DT9805 + DT9806 |
| 2     | 19          | `TaskSpec`, `AnalogInputVoltage`, `ThermocoupleInput`, `open_device`, `poll`, sync facade | none new                         | DT9805 + DT9806 (K-type TC plugged) |
| 3     | ~25         | `record`, `record_polled`, `DaqBlock`, `BufferPlan`, all sinks, `RawCountsSink` | All sinks; `dtol-capture`        | DT9805 + signal generator |
| 4     | 9           | `AnalogOutputVoltage`, `DigitalInputPort`, `DigitalOutputPort`, `DigitalLine`, `session.write` | `dtol-read`, `dtol-info`         | DT9806 + loopback wiring |
| 5     | ~20         | Counter/Timer specs, `Tachometer`, `RetriggerSpec`, `start_synchronized`     | none new                         | DT9806 + encoder + signal generator |
| 6     | ~20         | `RtdInput`, `ThermistorInput`, `IepeInput`, `StrainInput`, `BridgeInput`, TEDS helpers | none new                         | DT9805 + RTD + IEPE accel + strain gauge |
| 7     | ~5          | Pre-trigger modes, Sync Bus, auto-calibrate                                  | none new                         | DT9805 + DT9806 dual-board |

---

## 13. Risk Register (per-phase, with mitigations)

| Phase | Risk                                                                  | Mitigation                                                                                          |
|-------|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| 1     | Type aliases wrong (HDRVR is `c_void_p`? `HANDLE`? `c_uint32`?)        | §3.1 header verification gate — every alias has a `docs/decisions.md` entry pre-bind.                |
| 1     | Prototype `argtypes` wrong, esp. `LPARAM` vs `LONG` truncation         | Binding-test signatures parametrised; AST-level `_check` assertion in `test_api_all_methods_check`.  |
| 1     | DLL discovery fails confusingly on first install                      | `dtol-diag` is built specifically for this. Failure messages include all candidate paths tried.      |
| 1     | NIST ITS-90 coefficient transcription wrong                           | Hand-calculated reference vectors per TC type in `tests/unit/test_utils.py`.                         |
| 2     | MULTI_SENSOR ordering bug (silent wrong-data)                         | Builder unconditionally re-types on MULTI_SENSOR; `FakeDtolBackend` rejects out-of-order.            |
| 2     | TC sentinel coerced to plausible float                                | `capi.conversion.detect_thermocouple_sentinel` at the boundary; tests cover all three sentinels.     |
| 3     | §12.3.2 bridge — ordering invariant violated                           | All 7 invariants enforced by both `FakeDtolBackend` and bridge code; one unit test per invariant.   |
| 3     | Notification mechanism mismatch (NOTIFYPROC silently never fires)     | Bench-confirmed 2026-05-28: use `olDaSetWndHandle` + Win32 message pump (§5.5). Hardware-only test asserts BUFFER_DONE arrives within 2 s of olDaStart at 100 Hz × 8 channels. |
| 3     | Missing second `olDaConfig` after SetWndHandle (buffers stuck in IP)  | Bench-confirmed mandatory. Both fake and real backends enforce "config-twice" in `register_notification` + `commit`; integration test asserts SDK queue transitions occur. |
| 3     | Missing `olDaSetDmaUsage(0)` on no-DMA boards (buffers never rotate)  | Builder calls SetDmaUsage(min(1, NUMDMACHANS)) unconditionally; hardware test asserts BUFFER_DONE on DT9805 (NUMDMACHANS=0).                                                                                  |
| 3     | Wrong-rev SDK constants (OLDADEFS.bas vs OLDADEFS.H)                  | §1.4a constant verification gate; bench probe (`scripts/bench_probe_continuous.py`) reads back every constant via `olDaGetSSCaps` / `olDaGet*` to confirm. |
| 3     | CFUNCTYPE/WNDPROC GC bug (callback wrapper freed mid-acquisition)     | Strong ref on backend keyed by `id(hdass)` (WNDPROC + WNDCLASS + HWND + pump thread); regression test under GC pressure. |
| 3     | BufferPool use-after-free                                              | `BufferState` enum tracked per buffer; read on RELEASED raises immediately.                          |
| 3     | OVERRUN/UNDERRUN classification mis-routes to wrong DtolError subclass | Per-code classification table; one test per documented OLERR_* code.                                  |
| 3     | RawCountsSink GIL contention at high rates                            | `np.ndarray.tofile` releases GIL during I/O; profile under 50 kS/s; fall back to bg-writer thread.   |
| 3     | Recorder shutdown deadlocks on never-firing trigger                   | `close(graceful=False)` aborts by default; `graceful=True` is opt-in with timeout.                   |
| 4     | AO write outside safe range silently clamped                          | Validation runs before SDK call; never clamp.                                                        |
| 4     | UNDERRUN on continuous AO due to drainer lag                          | Same buffer-pool sizing rule; default 4 buffers; report `underruns_observed` in summary.             |
| 5     | Quadrature decoder cumulative-count overflow                          | 32-bit counter wrap detection in `tasks/models.py:CounterReading`.                                   |
| 5     | `start_synchronized` step ordering wrong (pre-start before put)       | Manager method has its own ordering test; `FakeDtolBackend` enforces.                                |
| 6     | Continuous-TC interleaved CJC wrong stride                            | Synthetic-buffer round-trip test in `tests/unit/test_conversion_deinterleave.py`.                    |
| 6     | Application-side ITS-90 path triggered when not implemented           | `DtolCapabilityError` at configure time with precise message.                                        |
| 7     | Long-duration soak surfaces buffer leak                               | `tracemalloc` snapshots every hour; `BufferState` counts monitored continuously.                     |
| 7     | Lock relaxation breaks cross-subsystem reentrancy                     | Relaxation must be earned by documented bench experiment; default conservative.                      |

---

## 14. Bench Setup (one-time)

To execute phases 2–7, the maintainer needs the following hardware in the lab:

| Item                                    | Phase first needed | Notes                                                                 |
|-----------------------------------------|--------------------|----------------------------------------------------------------------|
| DT9805 USB module                       | 1                  | The primary v0.1 target. Must show up in Open Layers Control Panel.   |
| DT9806 USB module                       | 1                  | Same vendor, more subsystems. Needed for Phase 4+.                    |
| Two K-type thermocouples (bench-calibrated) | 2              | For TC poll smoke tests and Phase 3 continuous TC.                    |
| 100 Ω PT100 RTD                         | 6                  | For RTD spec tests.                                                   |
| Strain gauge (foil, 350 Ω)              | 6                  | For strain spec tests.                                                |
| IEPE accelerometer                      | 6                  | For IEPE spec tests.                                                  |
| Signal generator (10 Hz–100 kHz)         | 3                  | For continuous AI rate validation and tachometer tests.               |
| Function generator + scope              | 4, 5               | For AO loopback and frequency / pulse-width measurement.              |
| Quadrature encoder                      | 5                  | For quadrature decoder validation.                                    |
| Loopback wiring AO0→AI0, DO0→DI0, CTR_OUT→CTR_IN | 4         | Open Question 7 — confirm with maintainer.                            |
| Dedicated bench Windows machine (self-hosted CI runner) | 1     | For `hardware.yml` workflow.                                          |
| Battery-backed UPS                      | 7                  | For 24-hour soak tests to not die from a power blip.                  |

---

## 15. Test Taxonomy

Five lanes, growing across phases:

| Lane           | Runs on                            | First populated | Purpose                                                  |
|----------------|------------------------------------|------------------|----------------------------------------------------------|
| `unit/`        | Win + Ubuntu + macOS, py3.13/3.14  | Phase 0          | `FakeDtolBackend`-only; no real DLL, no hardware         |
| `integration/` | Win + Ubuntu + macOS, py3.13/3.14  | Phase 1          | End-to-end against the fake; spec construction → poll/record |
| `binding/`     | Windows only, py3.13/3.14          | Phase 1          | Real ctypes, stub DLL (`tests/fixtures/fake_dlls/`)      |
| `hardware/`    | Self-hosted Windows runner only    | Phase 1          | Real DT9805/DT9806 attached; opt-in env-var gates        |
| `slow/`        | Subset of hardware                  | Phase 3          | 60-min + 24-hour soaks; opt-in via `DTOLLIB_ENABLE_SLOW_TESTS=1` |

Marker convention per `design.md` §22.3. The CI matrix in §22.4 of `design.md` shows the workflow split.

---

## 16. CI Matrix Evolution by Phase

| Phase | `ci.yml` lint | `ci.yml` type | `ci.yml` test (unit+integration) | `binding.yml` | `hardware.yml` | `docs.yml` | `release.yml` |
|-------|---------------|---------------|----------------------------------|----------------|-----------------|------------|---------------|
| 0     | ✓             | ✓             | ✓                                | —              | —               | ✓          | ✓             |
| 1     | ✓             | ✓             | ✓                                | ✓              | ✓ (opt-in)     | ✓          | ✓             |
| 2     | ✓             | ✓             | ✓                                | ✓              | ✓               | ✓          | ✓             |
| 3+    | ✓             | ✓             | ✓ + slow lane                    | ✓              | ✓               | ✓          | ✓             |

`hardware.yml` is always `workflow_dispatch`-gated. CI green does not depend on it.

---

## 17. Final Pre-Phase-0 Checklist

Before opening the first Phase 0 PR, confirm:

- [ ] Open Question 1 resolved (package name → `dtollib` or alternative).
- [ ] Open Question 2 resolved (64-bit confirmed as bring-up target).
- [ ] GitHub repo created at the documented org/name.
- [ ] PyPI namespace reserved (push a 0.0.0a0 placeholder).
- [ ] Local DataAcq SDK install verified on the maintainer Windows machine — `oldaapi64.dll` and `olmem64.dll` discoverable at default paths.
- [ ] Open Layers Control Panel shows the bench DT9805 and DT9806 with the names documented (Open Question 4).
- [ ] `design.md` and this document committed to the new repo.
- [ ] `docs/decisions.md` created with the prologue placeholders.
- [ ] Self-hosted Windows CI runner registered with GitHub (or roadmap to register it during Phase 1).

When all boxes are checked, Phase 0 begins.

---

## References

- `design.md` — the architecture plan this document expands.
- `dasdk.md` — DataAcq SDK User's Manual (full text).
- `dasdk_digest.md` — technical digest of the SDK manual.
- `UMOpenLayers.md` — Open Layers .NET API reference (informs spec field names where the .NET API is the precedent).
- `c:\Users\gbellamy\Documents\git\nidaqlib\` — closest sibling; structural reference throughout.
- `c:\Users\gbellamy\Documents\git\alicatlib\`, `sartoriuslib\`, `watlowlib\` — older siblings; sink, manager, and sync-facade reference.
