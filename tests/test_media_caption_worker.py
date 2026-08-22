from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib import error as urlerror
from urllib import request as urlrequest

import media_caption_worker as worker


class MediaWorkerTests(unittest.TestCase):
    def test_trim_video_clamps_selection_and_writes_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mov"
            source.write_bytes(b"video")
            output = root / "clips" / "clip.mp4"
            engine = worker.MediaEngine([root])
            engine.ffmpeg = Path("ffmpeg")
            engine.ffprobe = Path("ffprobe")

            def run(arguments, timeout):
                self.assertGreaterEqual(timeout, 90)
                Path(arguments[-1]).write_bytes(b"clip")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(
                    engine,
                    "probe",
                    return_value={"duration": 10.0, "video_streams": [{}]},
                ),
                mock.patch.object(engine, "_run", side_effect=run) as execute,
            ):
                result = engine.trim_video(
                    {
                        "path": str(source),
                        "output_path": str(output),
                        "start": 2.25,
                        "end": 6.75,
                    }
                )

            self.assertEqual(str(output.resolve()), result["path"])
            self.assertEqual(2.25, result["start"])
            self.assertEqual(6.75, result["end"])
            self.assertEqual(4.5, result["duration"])
            self.assertTrue(output.is_file())
            arguments = execute.call_args.args[0]
            self.assertIn("libx264", arguments)
            self.assertIn("4.500", arguments)

    def test_audio_only_selection_is_wrapped_as_mp4_for_visual_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "voice.wav"
            source.write_bytes(b"audio")
            output = root / "clips" / "voice.mp4"
            engine = worker.MediaEngine([root])
            engine.ffmpeg = Path("ffmpeg")
            engine.ffprobe = Path("ffprobe")

            def run(arguments, timeout):
                self.assertGreaterEqual(timeout, 90)
                Path(arguments[-1]).write_bytes(b"wrapped-audio")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(
                    engine,
                    "probe",
                    return_value={
                        "duration": 8.0,
                        "video_streams": [],
                        "audio_streams": [{}],
                    },
                ),
                mock.patch.object(engine, "_run", side_effect=run) as execute,
            ):
                result = engine.trim_video(
                    {
                        "path": str(source),
                        "output_path": str(output),
                        "start": 1.0,
                        "end": 4.5,
                        "include_audio": False,
                    }
                )

            arguments = execute.call_args.args[0]
            self.assertEqual("audio", result["source_type"])
            self.assertTrue(result["audio_included"])
            self.assertIn("color=c=#24332d:s=1280x720:r=25", arguments)
            self.assertIn("aac", arguments)
            self.assertTrue(output.is_file())

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
