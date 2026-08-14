from pathlib import Path
import tempfile
import unittest
from urllib import error as urlerror
from urllib import request as urlrequest

import media_caption_worker as worker


class MediaWorkerTests(unittest.TestCase):
    def test_controller_uses_random_loopback_port_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = worker.MediaWorkerController([Path(directory)])
            health = controller.health()
            process = controller.process
            self.assertTrue(controller.running)
            self.assertGreater(controller.port, 0)
            self.assertEqual(1, health["protocol"])
            self.assertGreater(int(health["pid"]), 0)

            request = urlrequest.Request(
                f"http://127.0.0.1:{controller.port}/health",
                headers={"Authorization": "Bearer invalid-token"},
            )
            with self.assertRaises(urlerror.HTTPError) as raised:
                urlrequest.urlopen(request, timeout=2)
            self.assertEqual(401, raised.exception.code)

            controller.close()
            self.assertFalse(controller.running)
            self.assertIsNotNone(process.poll())

    def test_engine_rejects_files_outside_allowed_roots(self):
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            outside_file = Path(outside) / "private.mp4"
            outside_file.write_bytes(b"media")
            engine = worker.MediaEngine([Path(allowed)])
            with self.assertRaises(PermissionError):
                engine._input_path(outside_file)

    def test_tool_status_contains_no_runtime_process(self):
        status = worker.media_tool_status()
        self.assertEqual({"ffmpeg", "ffprobe"}, set(status))
        self.assertTrue(all(isinstance(value, str) for value in status.values()))


if __name__ == "__main__":
    unittest.main()
