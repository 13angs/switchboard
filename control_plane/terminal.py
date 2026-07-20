"""PTY terminal — spawn harness PTYs, bidirectional I/O proxy.

Spawns an interactive Claude/Codex process via pty+subprocess (Python stdlib
only).

Lifecycle (v2.1): the PTY is decoupled from any single WebSocket connection.
A PtyTerminal owns a persistent read thread that runs for the life of the child
process and forwards stdout to *the currently attached subscriber* (a WebSocket
connection), or drops it when none is attached (detached). This is what lets a
session survive detach — closing the browser detaches the subscriber but leaves
the PTY running, so a later reconnect re-attaches to the same live process.

The session registry (server.py) keys live PtyTerminals by session_id; kill
(SIGTERM) and reconnect (re-attach) both go through it.
"""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import termios
import threading
from typing import Callable, Optional, Protocol

from . import harness


class Subscriber(Protocol):
    """A sink for one attached connection. Implemented server-side (WebSocket).

    All three may be called from the PTY read thread; implementations must be
    safe to call concurrently with the connection's own socket writes.
    """

    def on_data(self, data: bytes) -> None: ...
    def on_control(self, msg: dict) -> None: ...
    def on_exit(self, code: Optional[int]) -> None: ...


def _winsz(rows: int, cols: int) -> bytes:
    """Pack a `struct winsize` — unsigned short rows × cols × xpix × ypix."""
    return struct.pack("HHHH", rows, cols, 0, 0)


class PtyTerminal:
    """A PTY child process whose stdout is routed to a swappable subscriber.

    Attributes:
        fd: master PTY file descriptor
        pid: child process PID
        session_id: Claude session id (may be set later for fresh sessions)
        closed: True once the child has exited and the reader has reaped it
        exit_code: child exit code (set on close)
    """

    def __init__(
        self,
        fd: int,
        pid: int,
        session_id: Optional[str] = None,
        harness_name: str = "claude",
        provider: str = "claude",
        output_observer: Optional[Callable[["PtyTerminal", bytes], None]] = None,
        input_observer: Optional[Callable[["PtyTerminal", bytes], None]] = None,
        close_observer: Optional[Callable[["PtyTerminal"], None]] = None,
    ):
        self.fd = fd
        self.pid = pid
        self.session_id = session_id
        self.harness = harness_name
        self.provider = provider
        self._output_observer = output_observer
        self._input_observer = input_observer
        self._close_observer = close_observer
        self.closed = False
        self.exit_code: Optional[int] = None
        self._sub: Optional[Subscriber] = None
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None

    # --- subscriber (connection) management ------------------------------

    def attach(self, sub: Subscriber) -> None:
        """Attach a subscriber. If the child already exited, notify at once."""
        with self._lock:
            self._sub = sub
            closed, code = self.closed, self.exit_code
        if closed:
            try:
                sub.on_exit(code)
            except Exception:
                pass

    def detach(self, sub: Optional[Subscriber] = None) -> None:
        """Detach the current subscriber; the PTY keeps running. If `sub` is
        given, only detach when it is still the current one — so a slow old
        connection closing late cannot detach a newer reconnect."""
        with self._lock:
            if sub is None or self._sub is sub:
                self._sub = None

    def notify_session_id(self, session_id: str) -> None:
        """Push a session_id control message to the current subscriber (used
        after a fresh session's id is discovered)."""
        with self._lock:
            sub = self._sub
        if sub:
            try:
                sub.on_control({"type": "session_id", "id": session_id})
            except Exception:
                pass

    def _emit_data(self, data: bytes) -> None:
        if self._output_observer:
            try:
                self._output_observer(self, data)
            except Exception:
                pass
        with self._lock:
            sub = self._sub
        if sub:
            try:
                sub.on_data(data)
            except Exception:
                pass  # sink dead; the connection loop will detach on its own

    # --- PTY I/O ---------------------------------------------------------

    def write(self, data: bytes) -> None:
        try:
            os.write(self.fd, data)
        except OSError:
            return
        if self._input_observer:
            try:
                self._input_observer(self, data)
            except Exception:
                pass

    def resize(self, rows: int, cols: int) -> None:
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, _winsz(rows, cols))
        except OSError:
            pass

    # --- lifecycle -------------------------------------------------------

    def is_alive(self) -> bool:
        if self.closed:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def terminate(self) -> None:
        """SIGTERM the child. The reader thread observes EOF, reaps it, and
        calls its on_close (registry cleanup)."""
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass

    def start_reader(self, on_close) -> None:
        """Start the persistent read thread. Forwards stdout to the current
        subscriber (or drops it when detached). On child exit: reap the *specific*
        pid, mark closed, notify the current subscriber, then call on_close(self)
        so the registry can drop the entry."""

        def run():
            try:
                while True:
                    try:
                        data = os.read(self.fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break  # EOF — child exited
                    self._emit_data(data)
            finally:
                code = self._reap()
                with self._lock:
                    self.closed = True
                    self.exit_code = code
                    sub = self._sub
                if self._close_observer:
                    try:
                        self._close_observer(self)
                    except Exception:
                        pass
                if sub:
                    try:
                        sub.on_exit(code)
                    except Exception:
                        pass
                try:
                    on_close(self)
                except Exception:
                    pass
                try:
                    os.close(self.fd)
                except OSError:
                    pass

        self._reader = threading.Thread(target=run, daemon=True)
        self._reader.start()

    def _reap(self) -> int:
        """Reap this terminal's own child (never `-1`, which could steal
        another PtyTerminal's child in a multi-session registry)."""
        try:
            _, status = os.waitpid(self.pid, 0)
        except OSError:
            return -1
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        return -1


def spawn_harness(
    harness_name: str,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    rows: int = 24,
    cols: int = 80,
    provider: str = "claude",
    env: Optional[dict] = None,
    output_observer: Optional[Callable[[PtyTerminal, bytes], None]] = None,
    input_observer: Optional[Callable[[PtyTerminal, bytes], None]] = None,
    close_observer: Optional[Callable[[PtyTerminal], None]] = None,
) -> PtyTerminal:
    """Spawn a harness process in a PTY.

    Args:
        harness_name: `claude` or `codex`.
        session_id: If given, resume that session. If None, fresh session.
        cwd: Working directory for the process.
        rows, cols: Initial terminal dimensions.

    The caller must call `start_reader()` (after registering) to begin I/O.
    """
    cwd_arg = cwd or os.getcwd()
    cmd = harness.build_command(harness_name, session_id, cwd_arg, provider)

    pid, fd = pty.fork()
    if pid == 0:
        # Child: apply provider env overrides, set terminal size, chdir, exec.
        if env:
            for key, val in env.items():
                os.environ[key] = val
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, _winsz(rows, cols))
        except OSError:
            pass
        if cwd:
            try:
                os.chdir(cwd)
            except OSError:
                pass
        os.execvp(cmd[0], cmd)
        os._exit(127)  # exec failed

    # Parent: set master pty size too.
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, _winsz(rows, cols))
    except OSError:
        pass

    return PtyTerminal(
        fd=fd,
        pid=pid,
        session_id=session_id,
        harness_name=harness_name,
        provider=provider,
        output_observer=output_observer,
        input_observer=input_observer,
        close_observer=close_observer,
    )


def spawn_claude(
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    rows: int = 24,
    cols: int = 80,
    provider: str = "claude",
    env: Optional[dict] = None,
    output_observer: Optional[Callable[[PtyTerminal, bytes], None]] = None,
    input_observer: Optional[Callable[[PtyTerminal, bytes], None]] = None,
    close_observer: Optional[Callable[[PtyTerminal], None]] = None,
) -> PtyTerminal:
    """Backward-compatible Claude-only wrapper."""
    return spawn_harness(
        "claude",
        session_id=session_id,
        cwd=cwd,
        rows=rows,
        cols=cols,
        provider=provider,
        env=env,
        output_observer=output_observer,
        input_observer=input_observer,
        close_observer=close_observer,
    )
