"""PTY terminal — spawn harness PTYs, bidirectional I/O proxy.

Spawns an interactive Claude/Codex process via pty+subprocess (Python stdlib
only).

Lifecycle (v2.1): the PTY is decoupled from any single WebSocket connection.
A PtyTerminal owns a persistent read thread that runs for the life of the child
process and forwards stdout to *the currently attached subscriber* (a WebSocket
connection). This is what lets a session survive detach — closing the browser
detaches the subscriber but leaves the PTY running, so a later reconnect
re-attaches to the same live process.

Output produced while nothing is attached is not lost: it accumulates in a
bounded ring buffer that `attach()` replays to the next subscriber, so a
reconnect lands on a screen with context rather than a blank one (ADR-0027
§SD3). See `attach()` for the limits of that replay.

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


# ADR-0027 §SD3 — how much recent PTY output to keep for replay on re-attach.
# Bounded and in-memory: it is a screen-restoration aid, not a transcript (the
# jsonl store is the transcript).
REPLAY_BUFFER_BYTES = 256 * 1024


class PtyTerminal:
    """A PTY child process whose stdout is routed to a swappable subscriber.

    Attributes:
        fd: master PTY file descriptor
        pid: child process PID
        session_id: Claude session id (may be set later for fresh sessions)
        attach_key: server-assigned key that identifies this PTY from the
            moment it is spawned — the reconnect handle for a fresh session,
            whose `session_id` does not exist until the harness writes its
            jsonl (ADR-0028 §SD1). Set by the registry, not by this class.
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
        self.attach_key: Optional[str] = None
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
        # Recent output, kept whether or not anyone is attached (ADR-0027 §SD3).
        self._replay = bytearray()

    # --- subscriber (connection) management ------------------------------

    def attach(self, sub: Subscriber) -> None:
        """Attach a subscriber, replaying recent output first. If the child
        already exited, notify at once.

        ADR-0027 §SD3: the replay is what makes a reconnect land on a screen
        with context instead of a blank one — a blank screen is why a detached
        session used to get killed and resumed rather than re-attached.

        **It is best-effort, not a faithful screen.** Full-screen TUIs run on
        the terminal's alternate screen buffer, which discards everything that
        scrolls out of the viewport, so a byte buffer that begins mid-stream can
        replay a partial escape-sequence state and render something cosmetically
        wrong. The next full redraw corrects it. Do not treat this buffer as
        authoritative for what the screen contains.
        """
        with self._lock:
            self._sub = sub
            closed, code = self.closed, self.exit_code
            replay = bytes(self._replay)
        if replay:
            try:
                sub.on_data(replay)
            except Exception:
                pass
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
            # Buffer unconditionally — output produced while detached is
            # exactly what a reconnecting client needs (ADR-0027 §SD3).
            self._replay.extend(data)
            overflow = len(self._replay) - REPLAY_BUFFER_BYTES
            if overflow > 0:
                del self._replay[:overflow]
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
    model: Optional[str] = None,
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
        model: Pin the session's model (ADR-0030). Fresh spawns only — a resume
            re-enters a session that already has one.

    The caller must call `start_reader()` (after registering) to begin I/O.
    """
    cwd_arg = cwd or os.getcwd()
    cmd = harness.build_command(harness_name, session_id, cwd_arg, provider, model)

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
