"""Generic loopback request-rewriting proxy for agent integrations.

Some agent CLIs hardcode request shapes that strict OpenAI-compatible
servers reject (e.g. codex's Responses API requests omit ``text.format``,
which LM Studio refuses with ``missing_required_parameter``). The fix
without forking the agent is to drop a tiny localhost proxy between the
agent and its model server, rewriting the request body on the way out.

``ShimServer`` is the reusable transport: it listens on a random local
port, forwards every request to a configured upstream URL, and (if a
``rewriter`` callable is supplied) lets that callable modify the request
body before it goes upstream. Responses stream straight back to the
agent as bytes arrive, so SSE / ``text/event-stream`` traffic isn't
batched and the agent sees tokens at the same cadence as without the
shim.

The shim is intentionally narrow: per-agent quirks live in each
integration's rewriter, not here. To use it from a new integration:

    shim = ShimServer(upstream_url=endpoint, rewriter=my_rewriter)
    shim.start()
    try:
        # point the agent at shim.local_url, launch it
        ...
    finally:
        shim.stop()
"""
from __future__ import annotations

import http.client
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

# (body, method, path) -> rewritten_body. Return the body unchanged when
# the rewriter has no opinion. Bodies are raw bytes — JSON parsing is the
# rewriter's responsibility (so non-JSON paths pass through cheaply).
RequestRewriter = Callable[[bytes, str, str], bytes]


class ShimServer:
    def __init__(
        self,
        upstream_url: str,
        rewriter: RequestRewriter | None = None,
    ) -> None:
        # The upstream root — usually the bare endpoint without ``/v1``,
        # since agents append their own version-prefixed paths. We keep
        # any path component the user included (custom endpoints sometimes
        # mount the OpenAI API under e.g. ``/api``) and prepend it when
        # forwarding.
        self.upstream_url = upstream_url.rstrip("/")
        self.rewriter = rewriter
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Bind to 127.0.0.1:0 so the kernel picks a free port. ThreadingHTTPServer
        # gives us per-request threads, which matters for SSE responses (a
        # streaming response holds its socket for the whole turn and would
        # block a serial server from accepting follow-up requests).
        handler_class = type(
            "_ShimBoundHandler", (_ShimHandler,), {"_shim": self}
        )
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="conduit-shim",
            daemon=True,
        )
        self._thread.start()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("ShimServer.start() has not been called")
        return self._server.server_address[1]

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


class _ShimHandler(BaseHTTPRequestHandler):
    # Bound at class-creation time in ShimServer.start().
    _shim: ShimServer

    # Default BaseHTTPRequestHandler logs every request to stderr. The agent
    # has its own UI; the shim should be invisible unless something fails.
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002, ARG002
        return

    # Treat every HTTP method symmetrically — the rewriter decides whether
    # to touch the body. Codex only uses POST today, but extending later
    # (e.g. PATCH for model uploads) shouldn't need a code change here.
    def do_GET(self) -> None: self._proxy("GET")
    def do_POST(self) -> None: self._proxy("POST")
    def do_PUT(self) -> None: self._proxy("PUT")
    def do_DELETE(self) -> None: self._proxy("DELETE")
    def do_PATCH(self) -> None: self._proxy("PATCH")
    def do_HEAD(self) -> None: self._proxy("HEAD")

    def _proxy(self, method: str) -> None:
        body = self._read_body()
        if self._shim.rewriter is not None and body:
            try:
                body = self._shim.rewriter(body, method, self.path)
            except Exception as e:
                self._error(500, f"shim rewriter raised: {e!r}")
                return

        try:
            self._forward(method, body)
        except (OSError, http.client.HTTPException) as e:
            self._error(502, f"upstream error: {e}")

    def _read_body(self) -> bytes:
        # Codex (and every OpenAI-style client) sets Content-Length on
        # request bodies — no chunked uploads in this direction — so a
        # straight read of N bytes is enough.
        cl = self.headers.get("Content-Length")
        if not cl:
            return b""
        try:
            n = int(cl)
        except ValueError:
            return b""
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def _forward(self, method: str, body: bytes) -> None:
        url = urllib.parse.urlparse(self._shim.upstream_url)
        host = url.hostname or "127.0.0.1"
        port = url.port or (443 if url.scheme == "https" else 80)
        conn_cls = (
            http.client.HTTPSConnection
            if url.scheme == "https"
            else http.client.HTTPConnection
        )
        # 5-minute timeout: long enough to ride out a cold-started model
        # that's still loading weights, short enough that a truly hung
        # upstream surfaces as an error instead of dangling forever.
        conn = conn_cls(host, port, timeout=300)
        try:
            upstream_path = url.path.rstrip("/") + self.path

            # Strip headers we have to regenerate ourselves: Host (to point
            # at upstream's hostname), Content-Length (body length may have
            # changed), and Accept-Encoding (we don't decompress responses
            # so asking upstream for gzip would corrupt the stream).
            req_headers: dict[str, str] = {}
            for k, v in self.headers.items():
                if k.lower() in ("host", "content-length", "accept-encoding"):
                    continue
                req_headers[k] = v
            if body:
                req_headers["Content-Length"] = str(len(body))
            req_headers["Host"] = url.netloc
            req_headers["Accept-Encoding"] = "identity"

            conn.request(method, upstream_path, body=body or None, headers=req_headers)
            resp = conn.getresponse()

            # Forward the status line + headers, but drop hop-by-hop headers
            # that don't survive the proxy boundary intact. We always close
            # the downstream connection after one response (Connection: close
            # below); the agent will open a fresh socket for the next call.
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() in (
                    "transfer-encoding",  # http.client dechunks for us
                    "connection",         # we issue our own
                    "content-length",     # may diverge from actual streamed body length
                    "keep-alive",
                    "proxy-authenticate",
                    "proxy-authorization",
                    "te",
                    "trailers",
                    "upgrade",
                ):
                    continue
                self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()

            # 8 KiB read budget per loop iteration. http.client returns as
            # soon as any chunk is available (chunked encoding is decoded
            # for us), so SSE events of a few hundred bytes flush through
            # with no batching — the agent sees streaming tokens at the
            # same cadence as a direct connection.
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    # Agent disconnected mid-stream (e.g. user Ctrl+C'd).
                    # Nothing to recover; just stop forwarding.
                    return
        finally:
            conn.close()

    def _error(self, status: int, msg: str) -> None:
        sys.stderr.write(f"conduit shim: {msg}\n")
        body = json.dumps(
            {"error": {"message": msg, "type": "shim_error"}}
        ).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return
