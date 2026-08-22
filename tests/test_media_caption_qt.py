import os
import tempfile
import unittest
from pathlib import Path

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import media_caption_qt as qt
except ImportError:  # pragma: no cover - optional dependency
    QApplication = None
    qt = None


@unittest.skipIf(QApplication is None, "PySide6 optional dependency is not installed")
class QtWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = qt.ModernMainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_three_column_shell_and_all_runtime_routes(self):
        self.assertEqual(3, self.window.columns.count())
        self.assertEqual(2, self.window.backend_box.count())
        self.assertEqual(3, self.window.runtime_box.count())
        self.assertEqual("3.6.demo", qt.APP_VERSION)
        self.assertGreater(self.window.model_edit.count(), 0)

    def test_theme_switch_is_atomic_and_controls_keep_readable_styles(self):
        self.window.apply_theme("day")
        day_style = self.window.styleSheet()
        self.assertIn("#f2eee5", day_style)
        self.window.apply_theme("night")
        night_style = self.window.styleSheet()
        self.assertIn("#20242b", night_style)
        self.assertNotEqual(day_style, night_style)
        self.window.backend_box.setCurrentIndex(self.window.backend_box.findData("api"))
        self.app.processEvents()
        self.assertTrue(self.window.model_edit.isEnabled())

    def test_backend_and_runtime_disable_inapplicable_controls(self):
        self.window.backend_box.setCurrentIndex(self.window.backend_box.findData("local"))
        self.app.processEvents()
        self.assertFalse(self.window.provider_test_button.isEnabled())
        self.window.runtime_box.setCurrentIndex(self.window.runtime_box.findData("llamacpp"))
        self.app.processEvents()
        self.assertTrue(self.window.llama_context.isEnabled())
        self.assertFalse(self.window.local_folder_edit.isEnabled())

    def test_visible_actions_change_state_and_scan_materials(self):
        before = self.window.seed.text()
        self.window.randomize_seed()
        self.assertNotEqual(before, self.window.seed.text())
        self.window.select_mode("video")
        self.assertEqual("video", self.window.mode_box.currentData())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.mp4").write_bytes(b"not-a-real-video")
            self.window.folder_edit.setText(str(root))
            self.window.scan_current_folder()
            # The scanner deliberately reports unreadable media, but the click
            # still produces a deterministic log/status response.
            self.assertIn("扫描完成", self.window.log_edit.toPlainText())

    def test_toolbar_actions_are_wired_to_real_views(self):
        actions = {action.text(): action for action in self.window.findChildren(type(self.window.theme_action))}
        actions["视频反推"].trigger()
        self.assertEqual("video", self.window.mode_box.currentData())
        actions["图像打标"].trigger()
        self.assertEqual("image", self.window.mode_box.currentData())
        actions["系统说明"].trigger()
        self.assertIs(self.window.tabs.currentWidget(), self.window.system_info)


if __name__ == "__main__":
    unittest.main()
