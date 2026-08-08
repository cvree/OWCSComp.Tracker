#!/usr/bin/env python3
"""
range_server.py — a byte-range HTTP server, so seeking can be tested for real.

`http.server.SimpleHTTPRequestHandler` ignores `Range:` and always sends the
whole file. That is precisely the behaviour the sparse-acquisition work
exists to avoid, so testing against it would prove nothing: ffmpeg would
"seek" by downloading everything, exactly like the old path.

This handler implements `Range` properly (206 + Content-Range) and — the
part that makes it a measuring instrument rather than just a server —
counts the bytes it actually sends per file. That count is the ground truth
for the benchmark: it is measured at the wire, by the thing at the other
end, and it cannot be fooled by caching or estimation.
"""
from __future__ import annotations

import http.server
import os
import re
import socketserver
import threading

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

#: bytes served, per URL path. Module-level so a test can read it after the
#: server thread has done the work.
SERVED: dict[str, int] = {}
REQUESTS: dict[str, int] = {}
_LOCK = threading.Lock()


def reset() -> None:
    with _LOCK:
        SERVED.clear()
        REQUESTS.clear()


def total_served() -> int:
    with _LOCK:
        return sum(SERVED.values())


def total_requests() -> int:
    with _LOCK:
        return sum(REQUESTS.values())


#: A seek issues an OPEN-ENDED range (`bytes=N-`) and the client closes the
#: connection the moment it has the frame it wanted. Over a real network the
#: client's TCP window is what stops the server running ahead; over loopback
#: there is effectively no window, so a naive server pushes megabytes into
#: the socket buffer before it notices the pipe is gone — and then reports
#: those megabytes as "downloaded", which they were not.
#:
#: Shrinking the send buffer restores the back-pressure a real link has, so
#: the byte count means "bytes the client actually consumed" rather than
#: "bytes the kernel accepted". Without this the measurement over-reports a
#: single-frame seek by an order of magnitude.
SEND_BUFFER_BYTES = 16 * 1024


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files from `directory` with real byte-range support."""

    def log_message(self, *args):     # keep test output readable
        pass

    def setup(self):
        # `self.connection` only exists after super().setup(); the raw socket
        # is `self.request`, and the option has to be set before the buffer
        # is wrapped in a file object.
        import socket
        try:
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF,
                                    SEND_BUFFER_BYTES)
        except (OSError, AttributeError):
            pass
        super().setup()

    def _count(self, n: int) -> None:
        with _LOCK:
            SERVED[self.path] = SERVED.get(self.path, 0) + n
            REQUESTS[self.path] = REQUESTS.get(self.path, 0) + 1

    def do_GET(self):                 # noqa: N802
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        partial = False
        if rng:
            m = _RANGE_RE.search(rng)
            if m:
                a, b = m.group(1), m.group(2)
                if a:
                    start = int(a)
                    end = int(b) if b else size - 1
                elif b:                       # suffix range: last N bytes
                    start = max(0, size - int(b))
                partial = True
        if start >= size:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        end = min(end, size - 1)
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        sent = 0
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    sent += len(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            # ffmpeg closes the socket the moment it has the frame it wanted.
            # That is the whole point — count what actually crossed the wire.
            pass
        finally:
            self._count(sent)

    def do_HEAD(self):                # noqa: N802
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(os.path.getsize(path)))
        self.end_headers()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(directory: str) -> tuple[_Server, int]:
    """Start a range server on a free port. Returns (server, port)."""
    import functools
    handler = functools.partial(RangeHandler, directory=directory)
    httpd = _Server(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port
