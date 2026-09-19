"""Windows Host Runtime — ConPTY via `ctypes` (ADR-0052 SD2/SD4).

Binds kernel32's pseudo-console API (`CreatePseudoConsole`,
`ResizePseudoConsole`, `ClosePseudoConsole`) and process creation
(`CreateProcessW` with the `PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE` attribute)
directly through `ctypes` — never `pywinpty`, which would break
`tests/test_stdlib_purity.py`. `subprocess.list2cmdline` (stdlib) builds the
quoted Windows command line; nothing else outside `ctypes`/`os`/`msvcrt` is
used.

Every Windows-only symbol (`ctypes.wintypes`, `msvcrt`, `ctypes.WinDLL`) is
resolved lazily inside `_win32()`, never at module scope, so this module
stays importable on any platform — `tests/test_host_runtime.py` imports it
unconditionally to check exactly that.

Unlike a POSIX PTY's single bidirectional master fd, ConPTY exposes two
separate pipes (input the console reads from, output it writes to) — see
`control_plane.host.HostRuntime.spawn_pty`. `_Session` is this module's own
bookkeeping (not part of the `HostRuntime` protocol): each spawn keeps the
HPCON, the process handle, and both fds together, indexed by both the read
fd (what `resize()`/`close()` are called with) and the pid (what
`is_alive()`/`terminate()`/`reap()` are called with), because `PtyTerminal`
uses whichever identifier its own bookkeeping already has to hand.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF


@functools.lru_cache(maxsize=1)
def _win32():
    """Build (once) the ctypes bindings this module needs.

    Only ever reached on real Windows — `control_plane.host.get_host_runtime`
    constructs `WindowsHost` only when `platform.system() == "Windows"`."""
    import ctypes
    import msvcrt
    import types as _types
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    HPCON = wintypes.HANDLE
    PSA = ctypes.POINTER(SECURITY_ATTRIBUTES)

    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), PSA, wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL

    kernel32.CreatePseudoConsole.argtypes = [
        COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(HPCON),
    ]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT

    kernel32.ResizePseudoConsole.argtypes = [HPCON, COORD]
    kernel32.ResizePseudoConsole.restype = ctypes.c_long

    kernel32.ClosePseudoConsole.argtypes = [HPCON]
    kernel32.ClosePseudoConsole.restype = None

    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
        ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None

    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, PSA, PSA, wintypes.BOOL, wintypes.DWORD,
        ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    return _types.SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        msvcrt=msvcrt,
        kernel32=kernel32,
        COORD=COORD,
        STARTUPINFOEXW=STARTUPINFOEXW,
        PROCESS_INFORMATION=PROCESS_INFORMATION,
        HPCON=HPCON,
    )


@dataclass
class _Session:
    hpcon: int
    hprocess: int
    pid: int
    read_fd: int
    write_fd: int


class WindowsHost:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_fd: dict[int, _Session] = {}
        self._by_pid: dict[int, _Session] = {}

    def spawn_pty(
        self,
        argv: list[str],
        cwd: Optional[str],
        env: Optional[dict],
        rows: int,
        cols: int,
    ) -> tuple[int, int, int]:
        w = _win32()
        ctypes = w.ctypes

        # Two one-directional pipes stand in for the PTY master's single
        # bidirectional fd. `in_*` carries keystrokes host -> console;
        # `out_*` carries rendered output console -> host.
        in_read, in_write = wintypes_handle(), wintypes_handle()
        out_read, out_write = wintypes_handle(), wintypes_handle()
        if not w.kernel32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0):
            raise OSError("CreatePipe (input) failed")
        if not w.kernel32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
            raise OSError("CreatePipe (output) failed")

        hpcon = w.HPCON()
        hr = w.kernel32.CreatePseudoConsole(
            w.COORD(cols, rows), in_read, out_write, 0, ctypes.byref(hpcon)
        )
        if hr != 0:
            w.kernel32.CloseHandle(in_read)
            w.kernel32.CloseHandle(out_write)
            w.kernel32.CloseHandle(in_write)
            w.kernel32.CloseHandle(out_read)
            raise OSError(f"CreatePseudoConsole failed: HRESULT 0x{hr & 0xFFFFFFFF:08x}")

        attr_size = ctypes.c_size_t(0)
        w.kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attr_size))
        attr_list = ctypes.create_string_buffer(attr_size.value)
        attr_list_ptr = ctypes.cast(attr_list, ctypes.c_void_p)
        if not w.kernel32.InitializeProcThreadAttributeList(
            attr_list_ptr, 1, 0, ctypes.byref(attr_size)
        ):
            raise OSError("InitializeProcThreadAttributeList failed")
        if not w.kernel32.UpdateProcThreadAttribute(
            attr_list_ptr,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            hpcon,  # the HPCON value itself, not a pointer to it — matches
            # Microsoft's own sample (UpdateProcThreadAttribute(..., hpc, sizeof(hpc), ...))
            ctypes.sizeof(w.HPCON),
            None,
            None,
        ):
            raise OSError("UpdateProcThreadAttribute failed")

        startup = w.STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(w.STARTUPINFOEXW)
        startup.lpAttributeList = attr_list_ptr

        creation_flags = EXTENDED_STARTUPINFO_PRESENT
        env_buf = None
        if env:
            merged = dict(os.environ)
            merged.update(env)
            block = "\0".join(f"{k}={v}" for k, v in merged.items()) + "\0\0"
            env_buf = ctypes.create_unicode_buffer(block)
            creation_flags |= CREATE_UNICODE_ENVIRONMENT

        cmdline_buf = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        pi = w.PROCESS_INFORMATION()
        ok = w.kernel32.CreateProcessW(
            None,
            cmdline_buf,
            None,
            None,
            False,
            creation_flags,
            ctypes.cast(env_buf, ctypes.c_void_p) if env_buf is not None else None,
            cwd,
            ctypes.byref(startup.StartupInfo),
            ctypes.byref(pi),
        )
        w.kernel32.DeleteProcThreadAttributeList(attr_list_ptr)
        if not ok:
            err = ctypes.get_last_error()
            w.kernel32.ClosePseudoConsole(hpcon)
            w.kernel32.CloseHandle(in_read)
            w.kernel32.CloseHandle(out_write)
            w.kernel32.CloseHandle(in_write)
            w.kernel32.CloseHandle(out_read)
            raise OSError(f"CreateProcessW failed: WinError {err}")

        w.kernel32.CloseHandle(pi.hThread)
        # Only now — after CreateProcess has attached the child — release our
        # copies of the console-facing ends (Microsoft's documented order;
        # closing them right after CreatePseudoConsole instead silently
        # produced a pseudoconsole with no live child hooked to either pipe).
        w.kernel32.CloseHandle(in_read)
        w.kernel32.CloseHandle(out_write)

        read_fd = w.msvcrt.open_osfhandle(out_read.value, os.O_RDONLY | os.O_BINARY)
        write_fd = w.msvcrt.open_osfhandle(in_write.value, os.O_WRONLY | os.O_BINARY)

        session = _Session(
            hpcon=hpcon.value,
            hprocess=pi.hProcess,
            pid=pi.dwProcessId,
            read_fd=read_fd,
            write_fd=write_fd,
        )
        with self._lock:
            self._by_fd[read_fd] = session
            self._by_pid[pi.dwProcessId] = session
        return read_fd, write_fd, pi.dwProcessId

    def resize(self, fd: int, rows: int, cols: int) -> None:
        w = _win32()
        with self._lock:
            session = self._by_fd.get(fd)
        if session is None:
            return
        w.kernel32.ResizePseudoConsole(session.hpcon, w.COORD(cols, rows))

    def is_alive(self, pid: int) -> bool:
        w = _win32()
        with self._lock:
            session = self._by_pid.get(pid)
        if session is None:
            return False
        res = w.kernel32.WaitForSingleObject(session.hprocess, 0)
        return res == WAIT_TIMEOUT

    def terminate(self, pid: int) -> None:
        w = _win32()
        with self._lock:
            session = self._by_pid.get(pid)
        if session is None:
            return
        w.kernel32.TerminateProcess(session.hprocess, 1)

    def reap(self, pid: int) -> int:
        w = _win32()
        with self._lock:
            session = self._by_pid.get(pid)
        if session is None:
            return -1
        w.kernel32.WaitForSingleObject(session.hprocess, INFINITE)
        code = w.wintypes.DWORD(0)
        if not w.kernel32.GetExitCodeProcess(session.hprocess, w.ctypes.byref(code)):
            return -1
        return code.value

    def close(self, fd: int, write_fd: Optional[int] = None) -> None:
        w = _win32()
        with self._lock:
            session = self._by_fd.pop(fd, None)
            if session is not None:
                self._by_pid.pop(session.pid, None)
        if session is not None:
            w.kernel32.ClosePseudoConsole(session.hpcon)
            w.kernel32.CloseHandle(session.hprocess)
        try:
            os.close(fd)
        except OSError:
            pass
        if write_fd is not None and write_fd != fd:
            try:
                os.close(write_fd)
            except OSError:
                pass

    def resolve_executable(self, name: str) -> str:
        return shutil.which(name) or name


def wintypes_handle():
    """A fresh zero-initialized HANDLE-sized ctypes value for an out-param."""
    return _win32().wintypes.HANDLE()
