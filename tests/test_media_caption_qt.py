import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
