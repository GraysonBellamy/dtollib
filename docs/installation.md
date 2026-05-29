# Installation

## Python package

```bash
pip install dtollib
# or
uv pip install dtollib
```

Optional extras:

- `dtollib[parquet]` — adds `pyarrow` for the [Parquet sink](sinks.md).
- `dtollib[postgres]` — adds `asyncpg` for the [Postgres sink](sinks.md).
- `dtollib[docs]` — Zensical + mkdocstrings (documentation contributors).

## DataAcq SDK (Windows-only)

`dtollib` wraps the Data Translation **DataAcq SDK** through `ctypes`.
The SDK ships as a Windows installer from Data Translation's website;
install it before plugging in the DT9805 / DT9806 USB module.

After a default install:

| File | 64-bit Python (default) | 32-bit Python |
|---|---|---|
| `oldaapi*.dll` | `%SystemRoot%\System32\oldaapi64.dll` | `%SystemRoot%\SysWOW64\oldaapi32.dll` |
| `olmem*.dll` | `%SystemRoot%\System32\olmem64.dll` | `%SystemRoot%\SysWOW64\olmem32.dll` |
| Headers | `%ProgramFiles(x86)%\Data Translation\Win32\SDK\Include\` | same |

The loader resolves each DLL independently in this order:

1. Explicit path passed to
   `dtollib.capi.loader.load_openlayers(oldaapi_path=..., olmem_path=...)`.
   *Authoritative — if you pass an explicit path that doesn't exist,
   the loader raises rather than silently falling back.*
2. Environment-variable overrides:
    - `DTOLLIB_OLDAAPI_DLL`
    - `DTOLLIB_OLMEM_DLL`
3. Default Windows install path for the current Python bitness
   (see table above).
4. Bare DLL name — relies on the standard Windows DLL search order.

## Verify the install

```pwsh
dtol-diag
```

A successful install reports the loaded DLL paths, the two SDK
versions, and the enumerated boards.  Example output from a bench
with a DT9805 and a DT9806 connected:

```text
dtol-diag (dtollib 0.0.1.dev0+...)
platform=win32 python=3.14.4 bitness=64
------------------------------------------------------------
[OK] sdk: loaded oldaapi V7.0.0.7 + olmem V2.00.01
  python_bitness: 64
  platform: win32
  oldaapi_path: C:\Windows\System32\oldaapi64.dll
  olmem_path: C:\Windows\System32\olmem64.dll
  bitness: 64
[OK] boards: 2 board(s) enumerated
  count: 2
  - DT9805(00) model=DT9805(00) driver=Dt9800 instance=...
  - DT9806(00) model=DT9806(00) driver=Dt9800 instance=...
------------------------------------------------------------
overall: PASS
```

If the SDK is not installed or the DLLs are not on the resolution
chain, `dtol-diag` exits non-zero and prints the candidate paths it
tried.  See [troubleshooting.md](troubleshooting.md).

## Bitness considerations

The loader pre-checks Python interpreter bitness against the DLL
filename marker (`oldaapi32` vs `oldaapi64`) and raises
`DtolDependencyError` *before* the underlying `WinDLL` call emits an
opaque `OSError`.  In practice: install 64-bit Python and the 64-bit
SDK; the bench setup assumes 64-bit throughout.

## Non-Windows platforms

`pip install dtollib` works on Linux / macOS for type-checking and
ecosystem composition (e.g. a multi-instrument script importing both
`dtollib` and `alicatlib` type-checks on a Linux CI runner).  The
real backend raises `DtolDependencyError` at first SDK touch with a
clear "Windows-only" message; the `FakeDtolBackend` runs everywhere.
