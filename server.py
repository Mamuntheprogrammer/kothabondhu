"""Kotha Bondhu (কথাবন্ধু) — local HTTP server (standard library only).

Run ``python ai_version/run.py`` and the studio opens in your browser. Every
long operation is delegated to :mod:`studio` worker threads, and audio is
streamed to the page chunk-by-chunk over Server-Sent Events, so the interface
stays responsive even while the first model is loading.
"""

from __future__ import annotations

import json
import mimetypes
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
# Large enough for a 60-second 24 kHz reference clip sent as base64 JSON.
MAX_BODY = 12 * 1024 * 1024
# The standard library caps a request line at 64 KB and answers 414. This is a
# local-only server, so allow more headroom for proxied or extension-generated
# URLs and log them instead of silently failing.
MAX_REQUEST_LINE = 1024 * 1024


class BodyTooLarge(ValueError):
    """The upload exceeded MAX_BODY."""


if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from paths import configure_model_cache, using_bundled  # noqa: E402

# Prefer the models bundled inside ai_version/models, so a copied project runs
# offline without downloading anything. --models / AWAZ_CACHE_DIR override it.
configure_model_cache()

from studio import SAMPLE_RATE, Studio, open_in_file_manager, repair_bangla_manifest  # noqa: E402
import bn_voices  # noqa: E402
from cloning import CloneRequest  # noqa: E402
from core.config import cache_dir  # noqa: E402


# --------------------------------------------------------------------------- helpers


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class StudioHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, studio: Studio) -> None:
        super().__init__(address, handler)
        self.studio = studio
        self.started = time.time()

    def handle_error(self, request, client_address) -> None:
        # Browsers close SSE connections and audio fetches all the time; that
        # is normal, not an error worth printing a traceback for.
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KothaBondhu/2.2"
    verbose = os.environ.get("AWAZ_STUDIO_DEBUG") == "1"

    # ------------------------------------------------------------------ plumbing

    @property
    def studio(self) -> Studio:
        return self.server.studio  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        if self.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def log_error(self, fmt: str, *args) -> None:
        sys.stderr.write("studio: %s\n" % (fmt % args))

    def handle_one_request(self) -> None:
        """Like the stdlib version, but with a larger request-line limit."""
        try:
            self.raw_requestline = self.rfile.readline(MAX_REQUEST_LINE + 1)
            length = len(self.raw_requestline)
            if length > MAX_REQUEST_LINE:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if length > 65536:
                preview = self.raw_requestline[:200].decode("iso-8859-1", "replace").strip()
                self.log_error("long request line (%d bytes): %s", length, preview)
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            method_name = "do_" + self.command
            if not hasattr(self, method_name):
                self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Unsupported method (%r)" % self.command)
                return
            getattr(self, method_name)()
            self.wfile.flush()
        except TimeoutError as exc:
            self.log_error("Request timed out: %r", exc)
            self.close_connection = True

    def _send(
        self,
        status: int,
        body: bytes = b"",
        content_type: str = "application/octet-stream",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(
            status,
            json_bytes(payload),
            "application/json; charset=utf-8",
            {"Cache-Control": "no-store"},
        )

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0:
            return {}
        if length > MAX_BODY:
            # Drain the socket before replying, otherwise the unread upload is
            # parsed as the next request on this keep-alive connection and the
            # browser reports a network error instead of our JSON message.
            remaining = length
            while remaining > 0:
                block = self.rfile.read(min(65536, remaining))
                if not block:
                    break
                remaining -= len(block)
            raise BodyTooLarge(f"Upload is too large (limit {MAX_BODY // (1024 * 1024)} MB).")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _sse_start(self) -> bool:
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _sse(self, event: dict, event_id: int | None = None) -> bool:
        try:
            if event_id is not None:
                self.wfile.write(f"id: {event_id}\n".encode())
            payload = json.dumps(event, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _sse_ping(self) -> bool:
        try:
            self.wfile.write(b": keep-alive\n\n")
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    # ---------------------------------------------------------------------- GET

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path.startswith("/static/"):
                return self._static(path[len("/static/") :])
            if path == "/api/health":
                return self._json({"ok": True, "uptime": round(time.time() - getattr(self.server, "started", time.time()), 1)})
            if path == "/api/status":
                query = parse_qs(parsed.query)
                refresh = (query.get("refresh") or ["0"])[0] in {"1", "true", "yes"}
                return self._json(self.studio.status_payload(force=refresh))
            if path == "/api/models":
                return self._json(self.studio.models(force=True))
            if path == "/api/models/download":
                key = (parse_qs(parsed.query).get("key") or [""])[0]
                return self._download_stream(key)
            if path == "/api/recent":
                return self._json({"jobs": self.studio.recent_jobs()})
            if path == "/api/clone":
                return self._json(self.studio.clone.status())
            if path == "/api/clone/setup":
                query = parse_qs(parsed.query)
                profile = (query.get("profile") or ["cpu"])[0]
                force = (query.get("force") or ["0"])[0] in {"1", "true", "yes"}
                return self._clone_stream(self.studio.clone.setup_stream(profile, force=force))
            if path == "/api/clone/download":
                return self._clone_stream(self.studio.clone.download_stream())
            if path.startswith("/api/clone/voices/") and path.endswith("/audio"):
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 5:
                    return self._clone_voice_audio(parts[3])
            if path.startswith("/api/jobs/"):
                return self._job_route(path)
            if path == "/api/preview":
                return self._error("Use POST for previews.", 405)
            return self._error("Not found", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                traceback.print_exc()
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _static(self, relative: str) -> None:
        relative = unquote(relative).split("?")[0].lstrip("/")
        target = (WEB_DIR / relative).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._error("Not found", 404)
        if not target.is_file():
            return self._error("Not found", 404)
        mime, _ = mimetypes.guess_type(str(target))
        if target.suffix == ".js":
            mime = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif target.suffix == ".html":
            mime = "text/html; charset=utf-8"
        elif target.suffix == ".svg":
            mime = "image/svg+xml"
        body = target.read_bytes()
        self._send(
            200,
            body,
            mime or "application/octet-stream",
            # Always serve the current files on disk; a stale cached app.js can
            # hide newly added controls after an in-place update.
            {"Cache-Control": "no-store"},
        )

    def _download_stream(self, key: str) -> None:
        if not self._sse_start():
            return
        try:
            for event in self.studio.download_stream(key):
                if event.get("type") == "ping":
                    if not self._sse_ping():
                        return
                    continue
                if not self._sse(event):
                    return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001
            self._sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    def _clone_stream(self, events) -> None:
        if not self._sse_start():
            return
        try:
            for event in events:
                if event.get("type") == "ping":
                    if not self._sse_ping():
                        return
                    continue
                if not self._sse(event):
                    return
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001
            self._sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})

    def _clone_voice_audio(self, voice_id: str) -> None:
        try:
            payload = self.studio.clone.voice_audio(voice_id)
        except (KeyError, ValueError) as exc:
            return self._error(str(exc), 404)
        self._send(200, payload, "audio/wav", {"Cache-Control": "no-store"})

    def _job_route(self, path: str) -> None:
        # /api/jobs/<id>[/events | /chunks/<n> | /cancel | /export]
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3:
            return self._error("Not found", 404)
        job_id = parts[2]
        job = self.studio.get_job(job_id)
        if job is None:
            return self._error("That generation has expired. Run it again.", 404)

        if len(parts) == 3:
            return self._json(job.snapshot())
        action = parts[3]
        if action == "events":
            return self._job_events(job)
        if action == "chunks" and len(parts) >= 5:
            try:
                index = int(parts[4])
            except ValueError:
                return self._error("Bad chunk index", 400)
            if index < 0 or index >= len(job.chunk_wavs):
                return self._error("Chunk not ready", 404)
            return self._send(
                200,
                job.chunk_wavs[index],
                "audio/wav",
                {"Cache-Control": "no-store"},
            )
        if action == "export":
            fmt = (parse_qs(urlparse(self.path).query).get("format") or ["wav"])[0]
            return self._export(job, fmt)
        return self._error("Not found", 404)

    def _job_events(self, job) -> None:
        if not self._sse_start():
            return
        cursor = 0
        last_id = self.headers.get("Last-Event-ID")
        query = parse_qs(urlparse(self.path).query)
        after = query.get("after") or ([last_id] if last_id else [])
        try:
            if after:
                cursor = max(0, int(after[0]) + 1)
        except (TypeError, ValueError):
            cursor = 0

        while True:
            events, status, cursor = job.next_events(cursor)
            for offset, event in enumerate(events):
                if not self._sse(event, event_id=cursor - len(events) + offset):
                    return
            if status in {"done", "error", "cancelled"} and not events:
                break
            if not events:
                if not self._sse_ping():
                    return
        # Give the browser a beat to deliver the terminal event.
        time.sleep(0.05)

    def _export(self, job, fmt: str) -> None:
        from core.audio.export import ExportError

        if job.status in {"queued", "running"}:
            return self._error("Wait for generation to finish before exporting.", 409)
        try:
            path = self.studio.export(job, fmt)
        except (ValueError, ExportError) as exc:
            return self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            return self._error(f"{type(exc).__name__}: {exc}", 500)
        body = path.read_bytes()
        self._send(
            200,
            body,
            mimetypes.guess_type(str(path))[0] or "application/octet-stream",
            {
                "Content-Disposition": f'attachment; filename="{path.name}"',
                "Cache-Control": "no-store",
            },
        )

    # --------------------------------------------------------------------- POST

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/speak":
                return self._speak()
            if path == "/api/preview":
                return self._preview()
            if path == "/api/clone/token":
                return self._clone_token()
            if path == "/api/clone/quality":
                return self._clone_quality()
            if path == "/api/clone/voices":
                return self._clone_add_voice()
            if path.startswith("/api/clone/voices/") and path.endswith("/delete"):
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 5:
                    return self._clone_delete_voice(parts[3])
            if path == "/api/open-cache":
                open_in_file_manager(Path(self.studio.models()["cache_dir"]))
                return self._json({"ok": True})
            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                parts = [p for p in path.split("/") if p]
                if len(parts) >= 4:
                    return self._json({"ok": self.studio.cancel(parts[2])})
            return self._error("Not found", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except BodyTooLarge as exc:
            self._error(str(exc), 413)
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                traceback.print_exc()
            self._error(f"{type(exc).__name__}: {exc}", 500)

    def _speak(self) -> None:
        payload = self._body()
        text = str(payload.get("text") or "").strip()
        if not text:
            return self._error("Type something to speak first.", 400)
        if len(text) > 20_000:
            return self._error("That text is too long (20,000 character limit).", 400)
        language = str(payload.get("language") or "auto").lower()
        if language not in {"auto", "en", "bn"}:
            language = "auto"
        try:
            speed = float(payload.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        speed = min(max(speed, 0.5), 2.0)
        voice = payload.get("voice") or None
        voice = str(voice) if voice else None
        voice_bn = str(payload.get("voice_bn") or "").strip() or None
        if voice_bn == "mms_bn":
            voice_bn = None
        if voice_bn and voice_bn not in bn_voices.VOICES:
            return self._error("Unknown Bangla voice.", 400)
        steady_bn = bool(payload.get("steady_bn", True))
        clone_voice = str(payload.get("clone_voice") or "").strip() or None
        clone_bn_only = bool(payload.get("clone_bn_only", True))

        clone = self.studio.clone
        if clone_voice:
            try:
                clone.resolve_voice(clone_voice)
            except (KeyError, ValueError) as exc:
                return self._error(str(exc), 409)
            if not clone.status()["can_synthesize"]:
                return self._error(
                    "Voice cloning is not set up yet. Open Models → Clone to install it and add a voice.",
                    409,
                )

        request = CloneRequest(
            text=text,
            language=language,
            speed=speed,
            voice_en=voice,
            voice_bn=voice_bn,
            loudness_match=bool(payload.get("loudness", True)),
            clone_voice=clone_voice,
            clone_bn_only=clone_bn_only,
            steady_bn=steady_bn,
        )
        job = self.studio.submit(request)
        self._json({"ok": True, "job": job.id, "url": f"/api/jobs/{job.id}/events"})

    def _clone_token(self) -> None:
        payload = self._body()
        token = str(payload.get("token") or "").strip()
        if len(token) > 512:
            return self._error("That token is too long.", 400)
        self.studio.clone.save_token(token)
        return self._json(
            {
                "ok": True,
                "saved": bool(token),
                "message": (
                    "Token saved on this computer; it will be restored next time."
                    if token
                    else "Token cleared."
                ),
            }
        )

    def _clone_quality(self) -> None:
        payload = self._body()
        value = str(payload.get("quality") or "").strip()
        try:
            saved = self.studio.clone.set_quality(value)
        except ValueError as exc:
            return self._error(str(exc), 400)
        self.studio.clone.reload_engine()
        steps = self.studio.clone.QUALITY_STEPS[saved]
        return self._json(
            {
                "ok": True,
                "quality": saved,
                "steps": steps,
                "message": f"Using {steps} diffusion steps. The worker reloads on the next run.",
            }
        )

    def _clone_add_voice(self) -> None:
        try:
            meta = self.studio.clone.add_voice(self._body())
        except (KeyError, ValueError) as exc:
            return self._error(str(exc), 400)
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                import traceback

                traceback.print_exc()
            return self._error(f"{type(exc).__name__}: {exc}", 500)
        return self._json({"ok": True, "voice": meta}, 201)

    def _clone_delete_voice(self, voice_id: str) -> None:
        try:
            removed = self.studio.clone.delete_voice(voice_id)
        except ValueError as exc:
            return self._error(str(exc), 400)
        return self._json({"ok": True, "removed": removed})

    def _preview(self) -> None:
        from core.engines import ModelNotAvailable

        payload = self._body()
        voice = str(payload.get("voice") or "").strip()
        if not voice:
            return self._error("Choose a voice first.", 400)
        try:
            audio = self.studio.preview(voice)
        except ModelNotAvailable as exc:
            return self._error(str(exc), 409)
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                traceback.print_exc()
            return self._error(f"{type(exc).__name__}: {exc}", 500)
        self._send(200, audio, "audio/wav", {"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- main


def build_server(host: str, port: int) -> StudioHTTPServer:
    repair_bangla_manifest()
    studio = Studio()

    def warm() -> None:
        try:
            studio.models()
            studio.voices()
        except Exception:  # noqa: BLE001 — best effort
            pass

    threading.Thread(target=warm, name="studio-warm", daemon=True).start()

    httpd = StudioHTTPServer((host, port), Handler, studio)
    return httpd


def _pick_port(host: str, start: int, attempts: int = 20) -> int:
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"No free port between {start} and {start + attempts - 1}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="kotha-bondhu",
        description="Kotha Bondhu (কথাবন্ধু) web studio",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="preferred port (default: 8765)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument(
        "--models",
        metavar="PATH",
        help="model cache folder (default: ai_version/models when bundled, else the user cache)",
    )
    parser.add_argument("--debug", action="store_true", help="verbose request logging")
    args = parser.parse_args(argv)
    if args.debug:
        os.environ["AWAZ_STUDIO_DEBUG"] = "1"
        Handler.verbose = True
    if args.models:
        configure_model_cache(args.models)

    port = _pick_port(args.host, args.port)
    httpd = build_server(args.host, port)
    url = f"http://{args.host}:{port}/"
    print()
    # Keep the CLI banner ASCII-safe so it also prints on cp1252 consoles.
    # The Bengali name is rendered in the browser and the Markdown docs.
    print("  Kotha Bondhu  -  offline Bangla + English voice studio")
    print("  " + "-" * 54)
    print(f"  {url}")
    print(f"  models: {cache_dir()}" + ("  (bundled)" if using_bundled() else ""))
    print("  Ctrl+C to stop")
    print(flush=True)

    if not args.no_browser:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
