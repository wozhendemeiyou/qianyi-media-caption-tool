from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Iterable
from urllib import error as urlerror
from urllib import request as urlrequest


WORKER_PROTOCOL_VERSION = 1
WORKER_TOKEN_ENV = "QIANYI_WORKER_TOKEN"
WORKER_ROOTS_ENV = "QIANYI_WORKER_ALLOWED_ROOTS"
MAX_REQUEST_BYTES = 1024 * 1024


class MediaWorkerError(RuntimeError):
    pass


class MediaToolUnavailable(MediaWorkerError):
    pass


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _runtime_roots() -> list[Path]:
    roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        roots.append(Path(bundle_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parent)
    unique: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def find_media_tool(name: str) -> Path | None:
    executable_name = f"{name}.exe" if os.name == "nt" else name
    relative_candidates = (
        Path("assets") / "media" / executable_name,
        Path("resources") / "media" / executable_name,
        Path("media") / executable_name,
        Path(executable_name),
    )
    for root in _runtime_roots():
        for relative in relative_candidates:
            candidate = root / relative
            if candidate.is_file():
                return candidate.resolve()
    discovered = shutil.which(name)
    return Path(discovered).resolve() if discovered else None


def media_tool_status() -> dict[str, Any]:
    return {
        "ffmpeg": str(find_media_tool("ffmpeg") or ""),
        "ffprobe": str(find_media_tool("ffprobe") or ""),
    }


def _is_within(path: Path, roots: Iterable[Path]) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)


class MediaEngine:
    def __init__(self, allowed_roots: Iterable[Path]):
        roots = [Path(root).resolve(strict=False) for root in allowed_roots]
        if not roots:
            raise MediaWorkerError("Worker 未配置允许访问的目录")
        self.allowed_roots = tuple(dict.fromkeys(roots))
        self.ffmpeg = find_media_tool("ffmpeg")
        self.ffprobe = find_media_tool("ffprobe")
        self._processes: set[subprocess.Popen[str]] = set()
        self._process_lock = threading.Lock()

    def _input_path(self, value: Any) -> Path:
        path = Path(str(value or "")).expanduser().resolve(strict=False)
        if not path.is_file():
            raise MediaWorkerError("媒体文件不存在")
        if not _is_within(path, self.allowed_roots):
            raise PermissionError("媒体文件不在当前任务允许访问的目录内")
        return path

    def _output_path(self, value: Any, directory: bool = False) -> Path:
        path = Path(str(value or "")).expanduser().resolve(strict=False)
        target = path if directory else path.parent
        if not _is_within(target, self.allowed_roots):
            raise PermissionError("输出路径不在当前任务允许访问的目录内")
        if directory:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _run(
        self,
        arguments: list[str],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        with self._process_lock:
            self._processes.add(process)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise MediaWorkerError("媒体处理超时，已停止子进程") from error
        finally:
            with self._process_lock:
                self._processes.discard(process)
        result = subprocess.CompletedProcess(
            arguments, process.returncode, stdout, stderr
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "媒体工具执行失败").strip()
            raise MediaWorkerError(detail[-1200:])
        return result

    def shutdown(self) -> None:
        with self._process_lock:
            processes = list(self._processes)
        for process in processes:
            try:
                process.terminate()
            except OSError:
                continue
        deadline = time.monotonic() + 1.5
        for process in processes:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.wait(timeout=remaining)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "protocol": WORKER_PROTOCOL_VERSION,
            "pid": os.getpid(),
            "capabilities": {
                "probe": bool(self.ffprobe),
                "frames": bool(self.ffmpeg and self.ffprobe),
                "audio": bool(self.ffmpeg),
            },
            "tools": {
                "ffmpeg": str(self.ffmpeg or ""),
                "ffprobe": str(self.ffprobe or ""),
            },
        }

    def probe(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.ffprobe is None:
            raise MediaToolUnavailable("未检测到 FFprobe；请安装 FFmpeg 或随软件打包媒体组件")
        source = self._input_path(payload.get("path"))
        result = self._run(
            [
                str(self.ffprobe),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(source),
            ],
            timeout=30,
        )
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as error:
            raise MediaWorkerError("FFprobe 返回了无效结果") from error
        streams = data.get("streams") if isinstance(data.get("streams"), list) else []
        format_info = data.get("format") if isinstance(data.get("format"), dict) else {}
        duration = 0.0
        try:
            duration = max(0.0, float(format_info.get("duration") or 0.0))
        except (TypeError, ValueError):
            pass
        return {
            "path": str(source),
            "duration": duration,
            "size": max(0, int(format_info.get("size") or source.stat().st_size)),
            "format": str(format_info.get("format_name") or ""),
            "video_streams": [stream for stream in streams if stream.get("codec_type") == "video"],
            "audio_streams": [stream for stream in streams if stream.get("codec_type") == "audio"],
        }

    def extract_frames(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.ffmpeg is None or self.ffprobe is None:
            raise MediaToolUnavailable("视频抽帧需要 FFmpeg 与 FFprobe")
        source = self._input_path(payload.get("path"))
        output_dir = self._output_path(payload.get("output_dir"), directory=True)
        frame_count = max(1, min(32, int(payload.get("frame_count") or 8)))
        max_width = max(256, min(4096, int(payload.get("max_width") or 1280)))
        quality = max(2, min(31, int(payload.get("quality") or 3)))
        probe = self.probe({"path": str(source)})
        duration = max(0.1, float(probe.get("duration") or 0.1))
        fps = min(8.0, max(0.001, frame_count / duration))
        output_pattern = output_dir / "frame-%03d.jpg"
        self._run(
            [
                str(self.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"fps={fps:.8f},scale='min({max_width},iw)':-2:force_original_aspect_ratio=decrease",
                "-frames:v",
                str(frame_count),
                "-q:v",
                str(quality),
                str(output_pattern),
            ],
            timeout=max(60.0, min(900.0, duration * 2.0)),
        )
        frames = sorted(output_dir.glob("frame-*.jpg"))[:frame_count]
        if not frames:
            raise MediaWorkerError("视频抽帧未生成任何图像")
        return {
            "frames": [str(path) for path in frames],
            "duration": duration,
        }

    def extract_audio(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.ffmpeg is None:
            raise MediaToolUnavailable("音频提取需要 FFmpeg")
        source = self._input_path(payload.get("path"))
        output_path = self._output_path(payload.get("output_path"))
        if output_path.suffix.casefold() != ".wav":
            raise MediaWorkerError("音频输出必须使用 WAV 格式")
        self._run(
            [
                str(self.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            timeout=900,
        )
        return {"audio": str(output_path), "size": output_path.stat().st_size}


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def serve_worker(port: int, token: str, allowed_roots: Iterable[Path]) -> int:
    engine = MediaEngine(allowed_roots)

    class Handler(BaseHTTPRequestHandler):
        server_version = "QianyiMediaWorker/1"
        sys_version = ""

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _authorized(self) -> bool:
            value = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            return hmac.compare_digest(value, expected)

        def _payload(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as error:
                raise MediaWorkerError("请求长度无效") from error
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise MediaWorkerError("请求内容过大")
            if not length:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise MediaWorkerError("请求不是有效 JSON") from error
            if not isinstance(payload, dict):
                raise MediaWorkerError("请求必须是 JSON 对象")
            return payload

        def do_GET(self) -> None:
            if not self._authorized():
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            if self.path != "/health":
                _json_response(self, 404, {"ok": False, "error": "not found"})
                return
            _json_response(self, 200, engine.health())

        def do_POST(self) -> None:
            if not self._authorized():
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            try:
                payload = self._payload()
                if self.path == "/probe":
                    result = engine.probe(payload)
                elif self.path == "/extract-frames":
                    result = engine.extract_frames(payload)
                elif self.path == "/extract-audio":
                    result = engine.extract_audio(payload)
                elif self.path == "/shutdown":
                    result = {"ok": True}
                    threading.Thread(
                        target=self.server.shutdown,
                        daemon=True,
                        name="worker-shutdown",
                    ).start()
                else:
                    _json_response(self, 404, {"ok": False, "error": "not found"})
                    return
            except PermissionError as error:
                _json_response(self, 403, {"ok": False, "error": str(error)})
                return
            except (MediaWorkerError, OSError, ValueError) as error:
                _json_response(self, 422, {"ok": False, "error": str(error)})
                return
            _json_response(self, 200, {"ok": True, "result": result})

    server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        engine.shutdown()
        server.server_close()
    return 0


class MediaWorkerController:
    def __init__(
        self,
        allowed_roots: Iterable[Path],
        startup_timeout: float = 8.0,
    ):
        roots = [Path(root).resolve(strict=False) for root in allowed_roots]
        if not roots:
            raise ValueError("Worker 至少需要一个允许访问的目录")
        self.allowed_roots = tuple(dict.fromkeys(roots))
        self.startup_timeout = max(1.0, float(startup_timeout))
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        self.process: subprocess.Popen[Any] | None = None
        self._lock = threading.RLock()
        self._closed = False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--qianyi-worker", "--port", str(self.port)]
        return [
            sys.executable,
            str(Path(__file__).resolve()),
            "--serve",
            "--port",
            str(self.port),
        ]

    def start(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise MediaWorkerError("媒体 Worker 已关闭")
            if self.running:
                return self._request_once("GET", "/health", None, timeout=2.0)
            self.port = self._available_port()
            self.token = secrets.token_urlsafe(32)
            environment = os.environ.copy()
            environment[WORKER_TOKEN_ENV] = self.token
            environment[WORKER_ROOTS_ENV] = json.dumps(
                [str(path) for path in self.allowed_roots], ensure_ascii=False
            )
            # Keep HTTP/diagnostic text deterministic without forcing Python's
            # site loader to decode third-party .pth files as UTF-8. Some
            # Windows Python installations contain locale-encoded .pth files
            # and would otherwise exit before the worker can bind its port.
            environment["PYTHONIOENCODING"] = "utf-8"
            self.process = subprocess.Popen(
                self._command(),
                cwd=str(_runtime_roots()[-1]),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
            deadline = time.monotonic() + self.startup_timeout
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    break
                try:
                    return self._request_once("GET", "/health", None, timeout=0.5)
                except (MediaWorkerError, OSError, urlerror.URLError) as error:
                    last_error = error
                    time.sleep(0.08)
            exit_code = self.process.poll()
            self._terminate_locked()
            detail = f"，退出码 {exit_code}" if exit_code is not None else ""
            raise MediaWorkerError(
                f"媒体 Worker 启动失败{detail}: {last_error or '启动超时'}"
            )

    def _request_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> dict[str, Any]:
        body = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urlrequest.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlrequest.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urlerror.HTTPError as error:
            try:
                result = json.loads(error.read().decode("utf-8"))
                detail = str(result.get("error") or error.reason)
            except (UnicodeError, json.JSONDecodeError):
                detail = str(error.reason)
            raise MediaWorkerError(detail) from error
        except (UnicodeError, json.JSONDecodeError) as error:
            raise MediaWorkerError("媒体 Worker 返回了无效结果") from error
        if not isinstance(result, dict) or not result.get("ok"):
            raise MediaWorkerError(str(result.get("error") or "媒体 Worker 请求失败"))
        nested = result.get("result")
        return nested if isinstance(nested, dict) else result

    def request(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        with self._lock:
            self.start()
            try:
                return self._request_once("POST", path, payload or {}, timeout)
            except (ConnectionError, OSError, urlerror.URLError):
                self._terminate_locked()
                if self._closed:
                    raise MediaWorkerError("媒体 Worker 已关闭")
                self.start()
                return self._request_once("POST", path, payload or {}, timeout)

    def health(self) -> dict[str, Any]:
        with self._lock:
            return self.start()

    def probe(self, path: Path, timeout: float = 30.0) -> dict[str, Any]:
        return self.request("/probe", {"path": str(path)}, timeout=timeout)

    def extract_frames(
        self,
        path: Path,
        output_dir: Path,
        frame_count: int = 8,
        max_width: int = 1280,
        timeout: float = 900.0,
    ) -> list[Path]:
        result = self.request(
            "/extract-frames",
            {
                "path": str(path),
                "output_dir": str(output_dir),
                "frame_count": frame_count,
                "max_width": max_width,
            },
            timeout=timeout,
        )
        return [Path(value) for value in result.get("frames") or ()]

    def extract_audio(
        self,
        path: Path,
        output_path: Path,
        timeout: float = 900.0,
    ) -> Path:
        result = self.request(
            "/extract-audio",
            {"path": str(path), "output_path": str(output_path)},
            timeout=timeout,
        )
        return Path(str(result.get("audio") or output_path))

    def _terminate_locked(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
            process = self.process
            if process is None:
                return
            if process.poll() is None:
                try:
                    self._request_once("POST", "/shutdown", {}, timeout=1.0)
                    process.wait(timeout=3)
                except (MediaWorkerError, OSError, subprocess.TimeoutExpired, urlerror.URLError):
                    self._terminate_locked()
            self.process = None

    def __enter__(self) -> "MediaWorkerController":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def run_worker_cli(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--qianyi-worker", action="store_true")
    parser.add_argument("--port", type=int, required=True)
    parsed, _unknown = parser.parse_known_args(arguments)
    token = os.environ.get(WORKER_TOKEN_ENV, "")
    if not token:
        raise SystemExit("Worker token is missing")
    try:
        roots_payload = json.loads(os.environ.get(WORKER_ROOTS_ENV, "[]"))
    except json.JSONDecodeError as error:
        raise SystemExit("Worker roots are invalid") from error
    roots = [Path(value) for value in roots_payload if isinstance(value, str)]
    return serve_worker(parsed.port, token, roots)


if __name__ == "__main__":
    raise SystemExit(run_worker_cli(sys.argv[1:]))
