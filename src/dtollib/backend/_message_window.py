"""Hidden Win32 message window + pump thread for SDK buffer-done events.

The DataAcq SDK delivers continuous-mode buffer-done / error events one of
two ways: an ``olDaSetNotificationProcedure`` callback, or window messages
posted via ``olDaSetWndHandle``. On the DT9805/DT9806 (SDK V7.0.0.7) the
notification-procedure callback **never fires** — the only working path is a
hidden message-only window with a Win32 message pump (docs/decisions.md,
"Bench-verified continuous-mode setup").

This module owns that machinery and nothing else. It is the single home of
all Win32 (``user32`` / ``kernel32``) calls in the library:

- One process-wide window class (atom cached so repeated registrations do
  not leak) with one pinned :data:`_WNDPROC`.
- One :class:`MessageWindow` per registered subsystem. Each owns a hidden
  ``HWND_MESSAGE`` window and a dedicated pump thread. **The pump thread
  creates and owns the window** (Win32 requires a window to be serviced by
  the thread that created it), runs ``GetMessage``/``DispatchMessage``, and
  destroys the window on clean stop.
- Each ``OLDA_WM_*`` message is translated to ``(msg_id, wparam, lparam)``
  and handed to the Python callback supplied at construction — the same
  ``callback(msg_id, wparam, lparam) -> int`` contract the callback bridge
  expects. The callback runs on the pump thread and must do minimal work
  (the bridge's callback just does ``queue.put_nowait``).

The module imports cleanly on non-Windows (for type-checking and the
in-memory fake); constructing a :class:`MessageWindow` off Windows raises.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import TYPE_CHECKING

from dtollib._logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["MessageWindow"]

_logger = get_logger("backend.message_window")

# Win32 message-loop constants.
_WM_QUIT = 0x0012
_WM_USER = 0x0400
# The SDK's OLDA_WM_* notification messages occupy WM_USER+100 .. WM_USER+114
# (see dtollib.capi.constants). We forward exactly that band to the callback
# and let DefWindowProc handle everything else.
_OLDA_WM_FIRST = _WM_USER + 100
_OLDA_WM_LAST = _WM_USER + 114
# CreateWindowEx parent that makes the window message-only (no UI, no
# taskbar entry, never visible, only receives posted/sent messages).
_HWND_MESSAGE = -3

_WNDCLASS_NAME = b"DtolMessageWindow"


if sys.platform == "win32":
    _LRESULT = ctypes.c_ssize_t  # pointer-sized signed (LRESULT)
    _WNDPROCTYPE = ctypes.WINFUNCTYPE(
        _LRESULT, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
    )

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _WNDCLASS(ctypes.Structure):
        _fields_ = (
            ("style", ctypes.c_uint),
            ("lpfnWndProc", _WNDPROCTYPE),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HANDLE),
            ("hIcon", wintypes.HANDLE),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCSTR),
            ("lpszClassName", wintypes.LPCSTR),
        )

    _user32.DefWindowProcA.argtypes = [
        wintypes.HWND,
        ctypes.c_uint,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.DefWindowProcA.restype = _LRESULT
    _user32.RegisterClassA.argtypes = [ctypes.POINTER(_WNDCLASS)]
    _user32.RegisterClassA.restype = wintypes.ATOM
    _user32.CreateWindowExA.argtypes = [
        wintypes.DWORD,
        wintypes.LPCSTR,
        wintypes.LPCSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_void_p,
    ]
    _user32.CreateWindowExA.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.GetMessageA.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        ctypes.c_uint,
        ctypes.c_uint,
    ]
    _user32.GetMessageA.restype = ctypes.c_int
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.TranslateMessage.restype = wintypes.BOOL
    _user32.DispatchMessageA.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageA.restype = _LRESULT
    _user32.PostThreadMessageA.argtypes = [
        wintypes.DWORD,
        ctypes.c_uint,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostThreadMessageA.restype = wintypes.BOOL
    _kernel32.GetModuleHandleA.argtypes = [wintypes.LPCSTR]
    _kernel32.GetModuleHandleA.restype = wintypes.HANDLE
    _kernel32.GetCurrentThreadId.argtypes = []
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    # Process-wide window class. Registered once; the atom is cached so
    # repeated MessageWindow constructions reuse the class instead of leaking
    # a fresh atom each time. The single WNDPROC dispatches to the right
    # window by HWND via _WINDOWS.
    _class_lock = threading.Lock()
    _class_atom: int | None = None
    # hwnd (int) -> callback, read by the shared WNDPROC on the pump thread.
    # Plain dict access is GIL-atomic for the get/set we do here.
    _CALLBACKS: dict[int, Callable[[int, int, int], int]] = {}

    def _dispatch_wndproc(
        hwnd: int, msg: int, wparam: int, lparam: int
    ) -> int:  # pragma: no cover - exercised only on hardware / Windows
        if _OLDA_WM_FIRST <= msg <= _OLDA_WM_LAST:
            callback = _CALLBACKS.get(int(hwnd) if hwnd else 0)
            if callback is not None:
                try:
                    callback(msg, wparam, lparam)
                except Exception:
                    _logger.exception("message-window callback raised; message dropped")
            return 0
        return int(_user32.DefWindowProcA(hwnd, msg, wparam, lparam))

    # Pinned for the process lifetime — the SDK/USER32 hold a raw pointer to
    # this trampoline; letting it be GC'd would crash the message pump.
    _WNDPROC = _WNDPROCTYPE(_dispatch_wndproc)

    def _ensure_class_registered() -> None:
        global _class_atom  # noqa: PLW0603 - process-wide one-time registration
        with _class_lock:
            if _class_atom is not None:
                return
            wndclass = _WNDCLASS()
            wndclass.lpfnWndProc = _WNDPROC
            wndclass.hInstance = _kernel32.GetModuleHandleA(None)
            wndclass.lpszClassName = _WNDCLASS_NAME
            atom = _user32.RegisterClassA(ctypes.byref(wndclass))
            if not atom:
                err = ctypes.get_last_error()
                raise OSError(err, f"RegisterClassA failed (error {err})")
            _class_atom = atom


class MessageWindow:
    """A hidden message-only window served by a dedicated pump thread.

    Construction starts the pump thread, waits for it to create the window,
    and exposes the resulting ``HWND`` via :attr:`hwnd`. Pass that handle to
    ``olDaSetWndHandle``. Call :meth:`close` to stop the pump and destroy the
    window.

    Args:
        callback: Invoked ``callback(msg_id, wparam, lparam)`` on the pump
            thread for each ``OLDA_WM_*`` message. Must do minimal work.

    Raises:
        RuntimeError: If constructed on a non-Windows platform.
        OSError: If the window class or window cannot be created.
    """

    def __init__(self, callback: Callable[[int, int, int], int]) -> None:
        if sys.platform != "win32":
            raise RuntimeError("MessageWindow is only available on Windows")
        self._callback = callback
        self._hwnd: int = 0
        self._thread_id: int = 0
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._closed = False
        self._thread = threading.Thread(
            target=self._pump,
            name="dtol-msgwin-pump",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        if self._start_error is not None:
            # Pump thread already exited; surface the failure to the caller.
            raise self._start_error

    @property
    def hwnd(self) -> int:
        """The window handle to pass to ``olDaSetWndHandle``."""
        return self._hwnd

    def close(self) -> None:
        """Stop the pump thread and destroy the window. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._thread_id:
            # Wake GetMessage with WM_QUIT so the loop exits and the window
            # is destroyed on its owning (pump) thread.
            _user32.PostThreadMessageA(self._thread_id, _WM_QUIT, 0, 0)
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            _logger.warning("message-window pump thread did not exit within 5s")

    def _pump(self) -> None:  # pragma: no cover - Windows message loop
        self._thread_id = int(_kernel32.GetCurrentThreadId())
        try:
            _ensure_class_registered()
            hwnd = _user32.CreateWindowExA(
                0,
                _WNDCLASS_NAME,
                b"dtol",
                0,
                0,
                0,
                0,
                0,
                _HWND_MESSAGE,
                None,
                _kernel32.GetModuleHandleA(None),
                None,
            )
            if not hwnd:
                err = ctypes.get_last_error()
                raise OSError(err, f"CreateWindowExA failed (error {err})")
            self._hwnd = int(hwnd)
            _CALLBACKS[self._hwnd] = self._callback
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
            return

        self._ready.set()

        msg = wintypes.MSG()
        while True:
            ret = _user32.GetMessageA(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                # 0 == WM_QUIT; -1 == error. Either way, stop pumping.
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageA(ctypes.byref(msg))

        _CALLBACKS.pop(self._hwnd, None)
        _user32.DestroyWindow(wintypes.HWND(self._hwnd))
