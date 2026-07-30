import csv
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image
from pillow_heif import from_pillow

import media_caption_core as core


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)
        self.headers = headers or {}

    def json(self):
        return self._payload


class MemorySecretStore(core.SecretStore):
    def __init__(self):
        self.value = ""

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class CoreTests(unittest.TestCase):
    def test_model_routes_are_split_by_plan(self):
        expected = {
            "seed-2.0-pro": core.CODING_CHAT_URL,
            "seed-1.6-vision": core.CODING_CHAT_URL,
            "seed-2.1-pro": core.STANDARD_CHAT_URL,
            "seed-2.1-pro-turbo": core.CODING_CHAT_URL,
        }
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)
            for model_key, expected_url in expected.items():
                calls = []

                def sender(method, url, **kwargs):
                    calls.append((method, url, kwargs))
                    return FakeResponse(payload={"choices": [{"message": {"content": "caption"}}]})

                client = core.CaptionClient(
                    core.MODELS[model_key], "secret", core.CancellationToken(), core.HttpTransport(sender)
                )
                self.assertEqual("caption", client.caption_image(image_path, "prompt"))
                self.assertEqual(core.MODELS[model_key].chat_url_for(), calls[0][1])
                if model_key != "seed-2.0-pro" or core.date.today() < core.SEED_2_0_PLAN_END_DATE:
                    self.assertEqual(expected_url, calls[0][1])
                self.assertEqual(core.MODELS[model_key].model_id, calls[0][2]["json"]["model"])

    def test_coding_plan_transition_and_turbo_model_are_date_aware(self):
        legacy = core.MODELS["seed-2.0-pro"]
        turbo = core.MODELS["seed-2.1-pro-turbo"]

        self.assertEqual("doubao-seed-2-1-turbo-260628", turbo.model_id)
        self.assertEqual(core.CODING_CHAT_URL, turbo.chat_url_for(core.date(2026, 8, 8)))
        self.assertEqual("Coding Plan", turbo.billing_label(core.date(2026, 8, 8)))
        self.assertEqual(core.CODING_CHAT_URL, legacy.chat_url_for(core.date(2026, 8, 7)))
        self.assertEqual(
            "Coding Plan（8月8日下线）",
            legacy.billing_label(core.date(2026, 8, 7)),
        )
        self.assertEqual(core.STANDARD_CHAT_URL, legacy.chat_url_for(core.date(2026, 8, 8)))
        self.assertEqual("按量计费", legacy.billing_label(core.date(2026, 8, 8)))

    def test_seed_2_0_shutdown_notice_window_and_suppression(self):
        settings = {"suppress_seed_2_0_shutdown_notice": False}
        self.assertFalse(
            core.should_show_seed_2_0_shutdown_notice(
                settings, core.date(2026, 7, 23)
            )
        )
        self.assertTrue(
            core.should_show_seed_2_0_shutdown_notice(
                settings, core.date(2026, 7, 24)
            )
        )
        self.assertTrue(
            core.should_show_seed_2_0_shutdown_notice(
                settings, core.date(2026, 8, 7)
            )
        )
        self.assertFalse(
            core.should_show_seed_2_0_shutdown_notice(
                settings, core.date(2026, 8, 8)
            )
        )
        settings["suppress_seed_2_0_shutdown_notice"] = True
        self.assertFalse(
            core.should_show_seed_2_0_shutdown_notice(
                settings, core.date(2026, 8, 7)
            )
        )

    def test_small_video_uses_model_route_and_correct_mime(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / "clip.mov"
            video_path.write_bytes(b"small-video")
            calls = []

            def sender(method, url, **kwargs):
                calls.append((url, kwargs))
                return FakeResponse(payload={"choices": [{"message": {"content": "video caption"}}]})

            client = core.CaptionClient(
                core.MODELS["seed-2.1-pro"], "key", core.CancellationToken(), core.HttpTransport(sender)
            )
            self.assertEqual("video caption", client.caption_video(video_path, "prompt"))
            self.assertEqual(core.STANDARD_CHAT_URL, calls[0][0])
            data_url = calls[0][1]["json"]["messages"][0]["content"][0]["video_url"]["url"]
            self.assertTrue(data_url.startswith("data:video/quicktime;base64,"))

    def test_billable_generation_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.jpg"
            Image.new("RGB", (20, 20)).save(path)
            calls = []

            def sender(method, url, **kwargs):
                calls.append(url)
                return FakeResponse(503, {"error": {"message": "busy"}})

            client = core.CaptionClient(
                core.MODELS["seed-2.1-pro"], "key", core.CancellationToken(), core.HttpTransport(sender)
            )
            with self.assertRaises(core.ApiError):
                client.caption_image(path, "prompt")
            self.assertEqual(1, len(calls))

    def test_scan_is_single_pass_and_reports_unreadable_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ignored = root / "DeliveryOptimization"
            ignored.mkdir()
            Image.new("RGB", (24, 24)).save(ignored / "ignored.jpg")
            Image.new("RGB", (24, 24)).save(root / "same.jpg")
            Image.new("RGB", (24, 24)).save(root / "same.png")
            (root / "broken.jpg").write_bytes(b"not an image")

            result = core.scan_media(root, "image")

            self.assertEqual(2, len(result.files))
            self.assertEqual(["broken.jpg"], [path.name for path in result.unreadable])
            self.assertEqual({"same.jpg", "same.png"}, {path.name for path in result.conflicts})
            self.assertEqual(1, result.ignored_directories)
            self.assertIn("同一个 TXT", next(iter(result.conflicts.values())))

    def test_scan_reports_missing_invalid_and_orphan_txt_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            matched = root / "matched.jpg"
            missing = root / "missing.png"
            invalid = root / "invalid.webp"
            for path in (matched, missing, invalid):
                Image.new("RGB", (24, 24), "white").save(path)
            core.write_caption(matched, "usable caption")
            core.caption_path_for(invalid).write_text("", encoding="utf-8")
            orphan = root / "orphan.txt"
            orphan.write_text("no media", encoding="utf-8")

            result = core.scan_media(root, "image")

            self.assertEqual([missing], result.missing_captions)
            self.assertEqual([invalid], result.invalid_captions)
            self.assertEqual([orphan], result.orphan_captions)

    def test_retry_subset_still_blocks_output_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "same.jpg"
            second = root / "same.png"
            Image.new("RGB", (24, 24)).save(first)
            Image.new("RGB", (24, 24)).save(second)
            calls = []

            def sender(method, url, **kwargs):
                calls.append(url)
                raise AssertionError("conflicting files must not reach HTTP")

            with mock.patch.object(core, "app_data_dir", return_value=root / "appdata"):
                runner = core.BatchRunner(lambda kind, payload: None, core.HttpTransport(sender))
                summary = runner.run(
                    root, "image", "prompt", "seed-2.1-pro", "key",
                    only_paths=[first, second],
                )
            self.assertEqual(2, summary.failed)
            self.assertFalse(calls)

    def test_caption_style_adds_format_constraint_without_replacing_prompt(self):
        original = "保留这段用户提示词"
        phrases = core.compose_prompt(original, "phrases")
        natural = core.compose_prompt(original, "natural", "红色手提包")
        self.assertIn(original, phrases)
        self.assertIn("词组标签", phrases)
        self.assertIn(original, natural)
        self.assertIn("自然语言", natural)
        self.assertIn("红色手提包", natural)
        self.assertIn("忽略背景", natural)

    def test_focus_language_and_trigger_word_are_deterministic(self):
        prompt = core.compose_prompt(
            "keep user prompt",
            "phrases",
            labeling_focus="style",
            output_language="en",
        )
        self.assertIn("keep user prompt", prompt)
        self.assertIn("风格 LoRA", prompt)
        self.assertIn("English phrase tags", prompt)
        self.assertEqual(
            "qianyi_style, cinematic light",
            core.prepend_trigger_word("cinematic light", "qianyi_style", "en"),
        )
        self.assertEqual(
            "qianyi_style, cinematic light",
            core.prepend_trigger_word(
                "qianyi_style, cinematic light", "qianyi_style", "en"
            ),
        )
        self.assertEqual(
            "芊熠，红色长裙",
            core.prepend_trigger_word("红色长裙", "芊熠", "zh"),
        )

    def test_local_backend_uses_folder_client_and_prepends_trigger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.jpg"
            model_folder = root / "local-model"
            model_folder.mkdir()
            Image.new("RGB", (24, 24), "white").save(image_path)

            class FakeLocalClient:
                def __init__(self, folder, token):
                    self.folder = folder

                def caption_image(self, path, prompt):
                    self.assertions = (path, prompt)
                    return "cinematic lighting"

            with mock.patch.object(core, "LocalCaptionClient", FakeLocalClient):
                with mock.patch.object(core, "app_data_dir", return_value=root / "appdata"):
                    runner = core.BatchRunner(lambda kind, payload: None)
                    summary = runner.run(
                        root,
                        "image",
                        "prompt",
                        "seed-2.1-pro",
                        "",
                        backend="local",
                        local_model_folder=model_folder,
                        labeling_focus="style",
                        output_language="en",
                        trigger_word="qianyi_style",
                    )

            self.assertEqual(1, summary.success)
            self.assertEqual(
                "qianyi_style, cinematic lighting",
                core.caption_path_for(image_path).read_text(encoding="utf-8"),
            )

    def test_local_backend_rejects_non_model_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "config.json"):
                core.LocalCaptionClient(
                    Path(directory),
                    core.CancellationToken(),
                )

    def test_heic_content_with_jpg_extension_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "renamed.jpg"
            source = Image.new("RGB", (32, 32), "white")
            from_pillow(source).save(path, format="HEIF")
            self.assertTrue(core.is_readable_image(path))
            prepared = core.prepare_image(path)
            self.assertTrue(prepared.data)
            self.assertEqual("image/jpeg", prepared.mime_type)

    def test_exif_orientation_is_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rotated.jpg"
            image = Image.new("RGB", (40, 20), "white")
            exif = image.getexif()
            exif[274] = 6
            image.save(path, exif=exif)
            opened = core.open_image(path)
            try:
                self.assertEqual((20, 40), opened.size)
            finally:
                opened.close()

    def test_image_compression_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "noise.png"
            image = Image.frombytes("RGB", (512, 512), os.urandom(512 * 512 * 3))
            image.save(path)
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, "有界压缩"):
                core.prepare_image(path, size_limit=1024, max_attempts=10)
            self.assertLess(time.monotonic() - started, 3)

    def test_similar_image_detection_and_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            increasing = Image.new("L", (64, 64))
            increasing.putdata([x * 4 for _y in range(64) for x in range(64)])
            decreasing = Image.new("L", (64, 64))
            decreasing.putdata([(63 - x) * 4 for _y in range(64) for x in range(64)])
            first = root / "first.png"
            duplicate = root / "duplicate.png"
            different = root / "different.png"
            increasing.save(first)
            increasing.save(duplicate)
            decreasing.save(different)

            groups = core.find_similar_images(
                [first, duplicate, different], threshold=0
            )

            self.assertEqual(1, len(groups))
            self.assertEqual({first, duplicate}, set(groups[0]))
            token = core.CancellationToken()
            token.cancel()
            with self.assertRaises(core.CancelledError):
                core.find_similar_images([first, duplicate], token=token)

    def test_atomic_caption_write_and_empty_caption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"media")
            core.write_caption(path, " valid caption ")
            self.assertEqual("valid caption", core.caption_path_for(path).read_text(encoding="utf-8"))
            self.assertFalse(list(Path(directory).glob("*.tmp")))
            with self.assertRaises(ValueError):
                core.write_caption(path, "   ")

    def test_empty_and_error_txt_are_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.jpg"
            path.write_bytes(b"media")
            core.caption_path_for(path).write_text("", encoding="utf-8")
            self.assertFalse(core.has_usable_caption(path))
            core.caption_path_for(path).write_text("API Error: balance", encoding="utf-8")
            self.assertFalse(core.has_usable_caption(path))
            core.caption_path_for(path).write_text("real caption", encoding="utf-8")
            self.assertTrue(core.has_usable_caption(path))

    def test_settings_migrate_legacy_config_without_plaintext_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "config.json"
            settings_path = root / "settings.json"
            legacy.write_text(
                json.dumps({
                    "api_key": "legacy-secret",
                    "model": "doubao-seed-2-0-pro",
                    "chat_url": "wrong-global-route",
                    "concurrency": 32,
                }),
                encoding="utf-8",
            )
            secrets = MemorySecretStore()
            store = core.SettingsStore(settings_path, legacy, secrets)

            settings = store.load()

            persisted = settings_path.read_text(encoding="utf-8")
            self.assertEqual("legacy-secret", secrets.get())
            self.assertEqual("seed-2.0-pro", settings["model_key"])
            self.assertEqual(10, settings["concurrency"])
            self.assertNotIn("api_key", persisted)
            self.assertNotIn("chat_url", persisted)
            self.assertNotIn("legacy-secret", persisted)

    def test_settings_recover_from_malformed_persisted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings_path = root / "settings.json"
            settings_path.write_text(
                json.dumps({
                    "concurrency": "not-a-number",
                    "recent_folders": "C:/not-a-list",
                    "prompt_presets": ["not", "a", "mapping"],
                    "suppress_seed_2_0_shutdown_notice": "true",
                }),
                encoding="utf-8",
            )
            store = core.SettingsStore(
                settings_path,
                root / "legacy.json",
                MemorySecretStore(),
            )

            settings = store.load()

            self.assertEqual(3, settings["concurrency"])
            self.assertEqual([], settings["recent_folders"])
            self.assertEqual({}, settings["prompt_presets"])
            self.assertTrue(settings["suppress_seed_2_0_shutdown_notice"])

            settings["concurrency"] = "also-invalid"
            store.save(settings)
            persisted = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(3, persisted["concurrency"])

    def test_project_summary_reports_saved_progress_and_directory_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media-project"
            media.mkdir()
            data_root = root / "appdata"
            project_dir = core.project_data_path(media, data_root)
            project_dir.mkdir(parents=True)
            (project_dir / "state.json").write_text(
                json.dumps({
                    "status": "completed",
                    "finished_at": "2026-07-19T20:30:00",
                    "summary": {"total": 12, "success": 9, "skipped": 2, "failed": 1},
                }),
                encoding="utf-8",
            )

            summary = core.load_project_summary(media, data_root)

            self.assertEqual("media-project", summary["name"])
            self.assertTrue(summary["exists"])
            self.assertEqual("completed", summary["status"])
            self.assertEqual(12, summary["total"])
            self.assertEqual(9, summary["success"])
            self.assertEqual("2026-07-19T20:30:00", summary["updated_at"])
            media.rmdir()
            self.assertFalse(core.load_project_summary(media, data_root)["exists"])

    def test_delete_project_metadata_never_deletes_media_or_escapes_appdata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media-project"
            media.mkdir()
            media_file = media / "keep.jpg"
            media_file.write_bytes(b"user media")
            data_root = root / "appdata"
            metadata = core.project_data_path(media, data_root)
            metadata.mkdir(parents=True)
            (metadata / "state.json").write_text("{}", encoding="utf-8")

            self.assertTrue(core.delete_project_metadata(media, data_root))
            self.assertFalse(metadata.exists())
            self.assertEqual(b"user media", media_file.read_bytes())
            self.assertFalse(core.delete_project_metadata(media, data_root))

            outside = root / "outside-project-data"
            outside.mkdir()
            with mock.patch.object(core, "project_data_path", return_value=outside):
                with self.assertRaisesRegex(ValueError, "越界"):
                    core.delete_project_metadata(media, data_root)
            self.assertTrue(outside.is_dir())

    def test_retry_after_is_capped_and_backoff_is_cancellable(self):
        token = core.CancellationToken()
        called = threading.Event()

        def sender(method, url, **kwargs):
            called.set()
            return FakeResponse(429, text="slow", headers={"Retry-After": "999"})

        transport = core.HttpTransport(sender)
        errors = []

        def request():
            try:
                transport.request(
                    "GET", "https://example.invalid", token=token, api_key="key", attempts=2
                )
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=request)
        started = time.monotonic()
        thread.start()
        self.assertTrue(called.wait(1))
        token.cancel()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)
        self.assertIsInstance(errors[0], core.CancelledError)
        self.assertEqual(15, core.retry_delay(1, FakeResponse(headers={"Retry-After": "999"})))

    def test_http_error_includes_request_id_and_redacts_key(self):
        key = "very-secret-key"

        def sender(method, url, **kwargs):
            return FakeResponse(
                400,
                text=f"Authorization: Bearer {key}\ninvalid request",
                headers={"X-Request-Id": "req-123"},
            )

        transport = core.HttpTransport(sender)
        with self.assertRaises(core.ApiError) as raised:
            transport.request(
                "POST", "https://example.invalid", token=core.CancellationToken(), api_key=key
            )
        detail = str(raised.exception)
        self.assertIn("HTTP 400", detail)
        self.assertIn("req-123", detail)
        self.assertNotIn(key, detail)

    def test_batch_resume_skips_existing_without_http(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.jpg"
            Image.new("RGB", (24, 24)).save(image_path)
            core.write_caption(image_path, "existing")
            calls = []

            def sender(method, url, **kwargs):
                calls.append(url)
                raise AssertionError("HTTP should not be called")

            events = []
            with mock.patch.object(core, "app_data_dir", return_value=root / "appdata"):
                runner = core.BatchRunner(lambda kind, payload: events.append((kind, payload)), core.HttpTransport(sender))
                summary = runner.run(root, "image", "prompt", "seed-2.1-pro", "key", skip_existing=True)
            self.assertEqual(1, summary.skipped)
            self.assertEqual(0, summary.success)
            self.assertFalse(calls)
            self.assertEqual("completed", [payload["status"] for kind, payload in events if kind == "done"][0])

    def test_batch_incrementally_writes_caption_and_project_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.jpg"
            Image.new("RGB", (24, 24)).save(image_path)

            def sender(method, url, **kwargs):
                return FakeResponse(payload={"choices": [{"message": {"content": "new caption"}}]})

            data_root = root / "appdata"
            with mock.patch.object(core, "app_data_dir", return_value=data_root):
                runner = core.BatchRunner(lambda kind, payload: None, core.HttpTransport(sender))
                summary = runner.run(root, "image", "prompt", "seed-2.1-pro", "key")
            self.assertEqual(1, summary.success)
            self.assertEqual("new caption", core.caption_path_for(image_path).read_text(encoding="utf-8"))
            states = list(data_root.glob("projects/*/state.json"))
            self.assertEqual(1, len(states))
            state = json.loads(states[0].read_text(encoding="utf-8"))
            self.assertEqual("completed", state["status"])
            self.assertEqual("success", next(iter(state["items"].values()))["status"])
            self.assertEqual(1, len(list(data_root.glob("projects/*/run-*.jsonl"))))
            self.assertEqual(1, len(list(data_root.glob("projects/*/last-failures.csv"))))

    def test_batch_persists_failure_manifest_with_request_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.jpg"
            Image.new("RGB", (24, 24)).save(image_path)

            def sender(method, url, **kwargs):
                return FakeResponse(
                    400,
                    text='{"error":"bad request"}',
                    headers={"X-Request-Id": "request-failure-1"},
                )

            data_root = root / "appdata"
            with mock.patch.object(core, "app_data_dir", return_value=data_root):
                runner = core.BatchRunner(lambda kind, payload: None, core.HttpTransport(sender))
                summary = runner.run(root, "image", "prompt", "seed-2.1-pro", "key")
            self.assertEqual(1, summary.failed)
            manifests = list(data_root.glob("projects/*/last-failures.json"))
            self.assertEqual(1, len(manifests))
            failures = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual("image.jpg", failures[0]["path"])
            self.assertIn("request-failure-1", failures[0]["error"])

    def test_project_journal_throttles_state_snapshots_but_keeps_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            journal = core.ProjectJournal(media, root / "appdata")
            journal.start({"test": True})

            with mock.patch.object(
                core, "atomic_write_json", wraps=core.atomic_write_json
            ) as write_json:
                for index in range(100):
                    journal.record(
                        media / f"image-{index:04d}.jpg",
                        "success",
                        "caption",
                    )
                self.assertLessEqual(write_json.call_count, 1)
                journal.finish("completed", {"total": 100, "success": 100})

            events = journal.events_path.read_text(encoding="utf-8").splitlines()
            state = json.loads(journal.state_path.read_text(encoding="utf-8"))
            self.assertEqual(100, len(events))
            self.assertEqual(100, len(state["items"]))
            self.assertEqual("completed", state["status"])
            self.assertLessEqual(write_json.call_count, 2)

    def test_export_jsonl_and_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "folder" / "image.jpg"
            path.parent.mkdir()
            path.write_bytes(b"media")
            core.write_caption(path, "caption, with comma")
            jsonl_path = root / "captions.jsonl"
            csv_path = root / "captions.csv"
            self.assertEqual(1, core.export_jsonl([path], jsonl_path, root))
            self.assertEqual(1, core.export_csv([path], csv_path, root))
            row = json.loads(jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual("folder\\image.jpg", row["file_name"])
            with csv_path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual("caption, with comma", rows[0]["text"])

    def test_parallel_caption_reader_preserves_order_and_filters_invalid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = []
            expected = []
            for index in range(70):
                path = root / f"image-{index:03d}.jpg"
                path.write_bytes(b"media")
                caption = "ERROR: invalid" if index % 10 == 0 else f"caption {index}"
                core.caption_path_for(path).write_text(caption, encoding="utf-8")
                paths.append(path)
                if index % 10:
                    expected.append((path, caption))

            self.assertEqual(expected, list(core.iter_usable_captions(iter(paths))))


if __name__ == "__main__":
    unittest.main()
