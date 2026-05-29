# Troubleshooting

This page decodes `dtol-diag` output and walks through the most
common DataAcq SDK install issues.

Start by running:

```pwsh
dtol-diag
```

The output has two sections — `sdk` (DLL load + versions) and
`boards` (enumeration).  Each line is `[OK]` or `[FAIL]` followed by
a one-line summary.  Failures dump the candidate paths and the
underlying OS error to stderr.

## `[FAIL] sdk: failed to load DataAcq SDK`

The loader tried every path on its resolution chain and could not
load either `oldaapi64.dll` or `olmem64.dll`.  The error message
names every candidate it tried.

Common causes:

| Symptom | Cause | Fix |
|---|---|---|
| `file not found` against `C:\Windows\System32\oldaapi64.dll` | DataAcq SDK not installed | Install it from Data Translation's website |
| `WinDLL: [WinError 126] The specified module could not be found` | DLL exists but a dependency is missing (rare; DT-Open Layers usually ships standalone) | Re-install the SDK with administrator privileges |
| `bitness mismatch: Python is 32-bit but candidate ... names a 64-bit DLL` | 32-bit Python + 64-bit SDK install (or vice versa) | Install the matching bitness — usually means switching to 64-bit Python |
| `Windows-only` | Running on Linux / macOS | Expected — the real backend is Windows-only; tests still run via `FakeDtolBackend` |

If the SDK is installed in a non-default location, set:

```pwsh
$env:DTOLLIB_OLDAAPI_DLL = "C:\path\to\oldaapi64.dll"
$env:DTOLLIB_OLMEM_DLL   = "C:\path\to\olmem64.dll"
dtol-diag
```

## `[FAIL] boards: board enumeration failed`

The SDK loaded and the version query succeeded, but
`olDaEnumBoardsEx` returned a non-zero status.  Possible causes:

- The Open Layers Control Panel doesn't see the board — start there.
  Plug in the USB cable, reboot the DAQ if necessary, and re-launch
  the Control Panel.
- Driver service (`Dt9800`) is not running.  Check
  `services.msc → "DT9800 Service"` and restart it.
- Permissions: try running an elevated terminal (Run as
  Administrator).

## `[OK] boards: 0 board(s) enumerated`

The SDK loaded and enumeration succeeded but no boards were reported.
This is *not* an error — it means the SDK is healthy but no DT-Open
Layers device is currently visible.  Check:

1. Open Layers Control Panel — does it list the board?
2. USB cable seated; LED on the front panel.
3. For DT9805 specifically: the USB ID changes if you move it to a
   different USB port — re-register through the Control Panel after
   moving the cable.

## ECODE decoding

When the SDK reports an error in a runtime call (not enumeration),
the typed exception's `.context.ecode` field carries the raw
`OLSTATUS` value and `.context.ecode_message` carries the SDK's
decoded string.

The `DtolError` subclass is selected by:

1. **Per-code table** — documented async-error sentinels
   (`OLDA_WM_OVERRUN_ERROR`, `OLDA_WM_UNDERRUN_ERROR`,
   `OLDA_WM_TRIGGER_ERROR`) map to dedicated subclasses
   (`DtolBufferOverrunError` etc.).
2. **Range fallback** — the SDK groups codes by category (device,
   init, config, operation, buffer, memory).  Unknown codes within
   a range route to the category's typed exception.

For a full list see [`dtollib.errors`](api/errors.md) and the
`OLERRORS.H` cross-reference in
[decisions.md](decisions.md).

## `DtolDependencyError` on a freshly installed system

If `pip install dtollib` worked but the very first call to a
`DataAcqBackend` raises `DtolDependencyError`:

1. Run `dtol-diag` — the output names every path the loader tried.
2. If `python_bitness` ≠ DLL bitness, switch interpreters.
3. If the path table shows the right DLLs but `WinDLL` failed, run
   `dependency walker` against `oldaapi64.dll` to find the missing
   dependency (usually a Visual C++ runtime).

## Getting help

The repo's [decisions.md](decisions.md) records bench-verified
findings against specific SDK versions.  When filing an issue,
include the full `dtol-diag --json` output so reviewers see your
exact SDK version, DLL paths, and bitness.
