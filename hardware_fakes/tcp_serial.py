"""A pyserial-shaped object that serves a fake device over a local TCP
listener, so a fake can run inside the server process and the app can
reach it through its ordinary ``socket://host:port`` serial URL -- no
virtual serial port, no ``socat``, no WSL, on any OS.

Only the small part of pyserial's API the fakes actually use is
implemented: ``read``, ``readline``, ``write``, ``close``, ``is_open``,
``timeout`` and ``port`` (a label for log lines). A new client replaces
the previous one, so the app can disconnect and reconnect freely.
"""
from __future__ import annotations

import socket
import threading
import time
from typing import Optional


class TcpServerSerial:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, timeout: Optional[float] = 0.2):
        self.timeout = timeout
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(1)
        # accept() wakes up regularly to notice close(): closing a socket from
        # another thread doesn't reliably interrupt a blocked accept() on every OS.
        self._listener.settimeout(0.2)
        self.host = host
        self.tcp_port: int = self._listener.getsockname()[1]
        self.port = f"{host}:{self.tcp_port}"  # what the fakes print in their log lines
        self._conn: Optional[socket.socket] = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._connected = threading.Event()
        self._closed = threading.Event()
        threading.Thread(target=self._accept_loop, daemon=True, name="tcp-serial-accept").start()

    @property
    def url(self) -> str:
        """The pyserial URL the app connects to."""
        return f"socket://{self.host}:{self.tcp_port}"

    @property
    def is_open(self) -> bool:
        return not self._closed.is_set()

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _addr = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return  # listener closed
            conn.settimeout(None)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                old, self._conn = self._conn, conn
                self._buf.clear()
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            self._connected.set()

    def _fill(self, deadline: float) -> bool:
        """Waits for bytes until ``deadline``; True if any arrived."""
        while not self._closed.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            conn = self._conn
            if conn is None:
                self._connected.wait(min(remaining, 0.05))
                continue
            conn.settimeout(min(remaining, 0.2))
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                chunk = b""
            if not chunk:  # the client hung up
                with self._lock:
                    if self._conn is conn:
                        self._conn = None
                        self._connected.clear()
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            self._buf.extend(chunk)
            return True
        return False

    def _deadline(self) -> float:
        return time.monotonic() + (self.timeout if self.timeout is not None else 1e9)

    def read(self, size: int = 1) -> bytes:
        """Up to ``size`` bytes; b"" if none arrive within ``timeout``."""
        deadline = self._deadline()
        while not self._buf:
            if not self._fill(deadline):
                return b""
        data = bytes(self._buf[:size])
        del self._buf[:size]
        return data

    def readline(self) -> bytes:
        """One newline-terminated line; b"" if a whole line doesn't arrive within ``timeout``."""
        deadline = self._deadline()
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                line = bytes(self._buf[: i + 1])
                del self._buf[: i + 1]
                return line
            if not self._fill(deadline):
                return b""

    def write(self, data: bytes) -> int:
        """Sends to the connected client; dropped if nobody is connected (a real device just talks to nobody)."""
        with self._lock:
            conn = self._conn
        if conn is not None:
            try:
                conn.sendall(data)
            except OSError:
                pass
        return len(data)

    def close(self) -> None:
        self._closed.set()
        for s in (self._listener, self._conn):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
