from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import shutil
import tempfile
import threading
import time
from typing import Any, Callable

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

import media_caption_core as core


PROFILES = {
    "quick": {
        "scan": 1_000,
        "scan_bad": 5,
        "scan_orphans": 10,
        "batch": 300,
        "cancel": 500,
        "export": 2_000,
        "similarity": 500,
    },
    "standard": {
        "scan": 10_000,
        "scan_bad": 50,
        "scan_orphans": 100,
        "batch": 2_000,
        "cancel": 2_000,
        "export": 10_000,
        "similarity": 1_500,
    },
}

PERFORMANCE_LIMITS = {
    "quick": {
        "suite_seconds": 120,
        "peak_rss_mb": 512,
        "large_directory_scan": 30,
        "concurrent_batch_with_failures": 30,
        "cancellation_under_load": 10,
        "bulk_export": 30,
        "worst_case_similarity_group": 30,
    },
    "standard": {
        "suite_seconds": 300,
        "peak_rss_mb": 512,
        "large_directory_scan": 60,
        "concurrent_batch_with_failures": 60,
        "cancellation_under_load": 10,
        "bulk_export": 120,
        "worst_case_similarity_group": 60,
    },
}


@dataclass
class FakeResponse:
    status_code: int = 200
    payload: dict[str, Any] | None = None
    text: str = ""
    headers: dict[str, str] | None = None

    def json(self) -> dict[str, Any]:
        return self.payload or {}


class MemorySampler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self.peak_bytes = current_rss_bytes()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.05):
            self.peak_bytes = max(self.peak_bytes, current_rss_bytes())

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> int:
        self.peak_bytes = max(self.peak_bytes, current_rss_bytes())
        self._stop.set()
        self._thread.join(timeout=1)
        return self.peak_bytes


def current_rss_bytes() -> int:
    if os.name != "nt":
        try:
            import resource

            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return int(value * (1024 if platform.system() != "Darwin" else 1))
        except (ImportError, OSError):
            return 0

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess()
    get_memory_info = ctypes.windll.kernel32.K32GetProcessMemoryInfo
    get_memory_info.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    get_memory_info.restype = ctypes.c_int
    if not get_memory_info(process, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.WorkingSetSize)


def jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (48, 48), "#29cbe8").save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def clone_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def make_media_set(folder: Path, count: int, seed: Path, buckets: int = 20) -> list[Path]:
    paths = []
    for index in range(count):
        path = folder / f"set-{index % buckets:02d}" / f"image-{index:06d}.jpg"
        clone_file(seed, path)
        paths.append(path)
    return paths


def measure(name: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    rss_before = current_rss_bytes()
    sampler = MemorySampler()
    sampler.start()
    started = time.perf_counter()
    try:
        details = operation()
        passed = True
        error = ""
    except Exception as exception:
        details = {}
        passed = False
        error = f"{type(exception).__name__}: {exception}"
    elapsed = time.perf_counter() - started
    peak = sampler.stop()
    return {
        "name": name,
        "passed": passed,
        "seconds": round(elapsed, 3),
        "peak_rss_mb": round(peak / 1024 / 1024, 2),
        "rss_delta_mb": round((current_rss_bytes() - rss_before) / 1024 / 1024, 2),
        "error": error,
        **details,
    }


def stress_scan(base: Path, config: dict[str, int], seed: Path) -> dict[str, Any]:
    folder = base / "scan"
    setup_started = time.perf_counter()
    paths = make_media_set(folder, config["scan"], seed)
    usable = invalid = 0
    for index, path in enumerate(paths):
        if index % 4 == 0:
            core.caption_path_for(path).write_text("usable caption", encoding="utf-8")
            usable += 1
        elif index % 20 == 1:
            core.caption_path_for(path).write_text("ERROR: invalid caption", encoding="utf-8")
            invalid += 1
    for index in range(config["scan_bad"]):
        (folder / f"broken-{index:04d}.jpg").write_bytes(b"not-an-image")
    for index in range(config["scan_orphans"]):
        (folder / f"orphan-{index:04d}.txt").write_text("orphan", encoding="utf-8")
    setup_seconds = time.perf_counter() - setup_started

    def operation() -> dict[str, Any]:
        result = core.scan_media(folder, "image")
        expected_missing = len(paths) - usable - invalid
        assert len(result.files) == len(paths)
        assert len(result.unreadable) == config["scan_bad"]
        assert len(result.missing_captions) == expected_missing
        assert len(result.invalid_captions) == invalid
        assert len(result.orphan_captions) == config["scan_orphans"]
        return {
            "items": len(paths) + config["scan_bad"],
            "files": len(result.files),
            "unreadable": len(result.unreadable),
            "missing_captions": len(result.missing_captions),
            "invalid_captions": len(result.invalid_captions),
            "orphan_captions": len(result.orphan_captions),
            "setup_seconds": round(setup_seconds, 3),
        }

    return measure("large_directory_scan", operation)


def stress_batch(base: Path, count: int, seed: Path) -> dict[str, Any]:
    folder = base / "batch"
    setup_started = time.perf_counter()
    paths = make_media_set(folder, count, seed)
    setup_seconds = time.perf_counter() - setup_started
    sender_lock = threading.Lock()
    sender_calls = 0
    active_requests = 0
    peak_active_requests = 0
    failure_every = 17

    def sender(method: str, url: str, **kwargs: Any) -> FakeResponse:
        nonlocal sender_calls, active_requests, peak_active_requests
        with sender_lock:
            sender_calls += 1
            call_number = sender_calls
            active_requests += 1
            peak_active_requests = max(peak_active_requests, active_requests)
        try:
            time.sleep(0.01)
            if call_number % failure_every == 0:
                return FakeResponse(503, text="synthetic overload", headers={})
            return FakeResponse(
                payload={"choices": [{"message": {"content": "stress caption"}}]},
                headers={},
            )
        finally:
            with sender_lock:
                active_requests -= 1

    def operation() -> dict[str, Any]:
        original_app_data_dir = core.app_data_dir
        core.app_data_dir = lambda: base / "batch-appdata"
        try:
            event_counts: dict[str, int] = {}

            def on_event(kind: str, payload: dict[str, Any]) -> None:
                event_counts[kind] = event_counts.get(kind, 0) + 1

            runner = core.BatchRunner(on_event, core.HttpTransport(sender))
            summary = runner.run(
                folder,
                "image",
                "Describe the image.",
                "seed-2.1-pro",
                "offline-stress-key",
                concurrency=10,
                skip_existing=False,
                only_paths=paths,
            )
        finally:
            core.app_data_dir = original_app_data_dir
        expected_failed = count // failure_every
        assert summary.total == count
        assert summary.failed == expected_failed
        assert summary.success == count - expected_failed
        assert summary.cancelled == 0
        assert sum(1 for path in paths if core.caption_path_for(path).is_file()) == summary.success
        assert peak_active_requests == min(core.MAX_CONCURRENCY, count)
        return {
            "items": count,
            "success": summary.success,
            "failed": summary.failed,
            "concurrency": core.MAX_CONCURRENCY,
            "peak_active_requests": peak_active_requests,
            "requests": sender_calls,
            "events": event_counts,
            "setup_seconds": round(setup_seconds, 3),
        }

    return measure("concurrent_batch_with_failures", operation)


def stress_cancellation(base: Path, count: int, seed: Path) -> dict[str, Any]:
    folder = base / "cancel"
    setup_started = time.perf_counter()
    paths = make_media_set(folder, count, seed)
    setup_seconds = time.perf_counter() - setup_started
    request_count = 0
    request_lock = threading.Lock()
    enough_requests = threading.Event()

    def sender(method: str, url: str, **kwargs: Any) -> FakeResponse:
        nonlocal request_count
        with request_lock:
            request_count += 1
            if request_count >= core.MAX_CONCURRENCY * 4:
                enough_requests.set()
        time.sleep(0.01)
        return FakeResponse(
            payload={"choices": [{"message": {"content": "cancel caption"}}]},
            headers={},
        )

    def operation() -> dict[str, Any]:
        original_app_data_dir = core.app_data_dir
        core.app_data_dir = lambda: base / "cancel-appdata"
        runner = core.BatchRunner(lambda kind, payload: None, core.HttpTransport(sender))
        outcome: dict[str, Any] = {}

        def run() -> None:
            try:
                outcome["summary"] = runner.run(
                    folder,
                    "image",
                    "Describe the image.",
                    "seed-2.1-pro",
                    "offline-stress-key",
                    concurrency=10,
                    skip_existing=False,
                    only_paths=paths,
                )
            except Exception as exception:
                outcome["error"] = exception

        thread = threading.Thread(target=run, name="stress-cancel-runner")
        try:
            thread.start()
            assert enough_requests.wait(15), "batch did not reach cancellation point"
            cancelled_at = time.perf_counter()
            runner.cancel()
            thread.join(timeout=10)
            cancel_latency = time.perf_counter() - cancelled_at
        finally:
            core.app_data_dir = original_app_data_dir
        assert not thread.is_alive(), "batch did not stop within 10 seconds"
        assert "error" not in outcome, outcome.get("error")
        summary = outcome["summary"]
        accounted = summary.success + summary.skipped + summary.failed + summary.cancelled
        assert summary.total == count
        assert accounted == count
        assert summary.cancelled > 0
        assert cancel_latency < 5
        return {
            "items": count,
            "requests_before_stop": request_count,
            "success_before_stop": summary.success,
            "cancelled": summary.cancelled,
            "cancel_latency_seconds": round(cancel_latency, 3),
            "setup_seconds": round(setup_seconds, 3),
        }

    return measure("cancellation_under_load", operation)


def stress_export(base: Path, count: int, seed: Path) -> dict[str, Any]:
    folder = base / "export"
    setup_started = time.perf_counter()
    paths = make_media_set(folder, count, seed)
    for index, path in enumerate(paths):
        core.caption_path_for(path).write_text(
            f"caption {index}, with comma and UTF-8 中文", encoding="utf-8"
        )
    setup_seconds = time.perf_counter() - setup_started

    def operation() -> dict[str, Any]:
        jsonl_path = base / "captions.jsonl"
        csv_path = base / "captions.csv"
        jsonl_count = core.export_jsonl(paths, jsonl_path, folder)
        csv_count = core.export_csv(paths, csv_path, folder)
        assert jsonl_count == count
        assert csv_count == count
        assert sum(1 for _ in jsonl_path.open(encoding="utf-8")) == count
        assert sum(1 for _ in csv_path.open(encoding="utf-8")) == count + 1
        return {
            "items": count,
            "jsonl_bytes": jsonl_path.stat().st_size,
            "csv_bytes": csv_path.stat().st_size,
            "setup_seconds": round(setup_seconds, 3),
        }

    return measure("bulk_export", operation)


def stress_similarity(base: Path, count: int, seed: Path) -> dict[str, Any]:
    folder = base / "similarity"
    setup_started = time.perf_counter()
    paths = make_media_set(folder, count, seed)
    setup_seconds = time.perf_counter() - setup_started

    def operation() -> dict[str, Any]:
        groups = core.find_similar_images(paths, threshold=0)
        assert len(groups) == 1
        assert len(groups[0]) == count
        return {
            "items": count,
            "groups": len(groups),
            "largest_group": len(groups[0]),
            "setup_seconds": round(setup_seconds, 3),
        }

    return measure("worst_case_similarity_group", operation)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline pressure tests for MediaCaptionTool")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "analysis" / "stress-report.json",
    )
    args = parser.parse_args()
    config = PROFILES[args.profile]
    suite_started = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="media-caption-stress-") as directory:
        base = Path(directory)
        seed = base / "seed.jpg"
        seed.write_bytes(jpeg_bytes())
        results = [
            stress_scan(base, config, seed),
            stress_batch(base, config["batch"], seed),
            stress_cancellation(base, config["cancel"], seed),
            stress_export(base, config["export"], seed),
            stress_similarity(base, config["similarity"], seed),
        ]

    total_seconds = time.perf_counter() - suite_started
    limits = PERFORMANCE_LIMITS[args.profile]
    for result in results:
        violations = []
        duration_limit = limits[result["name"]]
        if result["seconds"] > duration_limit:
            violations.append(
                f"duration {result['seconds']}s exceeded {duration_limit}s"
            )
        if result["peak_rss_mb"] > limits["peak_rss_mb"]:
            violations.append(
                f"peak RSS {result['peak_rss_mb']}MB exceeded "
                f"{limits['peak_rss_mb']}MB"
            )
        if result["name"] == "cancellation_under_load":
            cancel_latency = result.get("cancel_latency_seconds", 0)
            if cancel_latency > 5:
                violations.append(
                    f"cancel latency {cancel_latency}s exceeded 5s"
                )
        result["duration_limit_seconds"] = duration_limit
        result["peak_rss_limit_mb"] = limits["peak_rss_mb"]
        result["performance_violations"] = violations
        result["passed"] = result["passed"] and not violations

    suite_violation = ""
    if total_seconds > limits["suite_seconds"]:
        suite_violation = (
            f"suite duration {total_seconds:.3f}s exceeded "
            f"{limits['suite_seconds']}s"
        )
    report = {
        "product": f"MediaCaptionTool {core.APP_VERSION if hasattr(core, 'APP_VERSION') else '3.2'}",
        "profile": args.profile,
        "offline": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "configuration": config,
        "performance_limits": limits,
        "total_seconds": round(total_seconds, 3),
        "suite_performance_violation": suite_violation,
        "passed": not suite_violation and all(result["passed"] for result in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
