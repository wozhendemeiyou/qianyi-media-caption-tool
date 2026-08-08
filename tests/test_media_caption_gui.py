from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest import mock

from PIL import Image, ImageChops

import media_caption_core as core
import media_caption_tool_v3 as gui


class MemorySecretStore(core.SecretStore):
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class GuiTests(unittest.TestCase):
    @staticmethod
    def _store(
        root_path: Path,
        api_key: str = "",
    ) -> core.SettingsStore:
        store = core.SettingsStore(
            root_path / "settings.json",
            root_path / "legacy.json",
            MemorySecretStore(api_key),
        )
        settings = store.load()
        store.save(settings)
        return store

    def test_main_window_uses_centered_restore_bounds_and_starts_maximized(self):
        root = mock.Mock()
        root.winfo_screenwidth.return_value = 1920
        root.winfo_screenheight.return_value = 1080

        geometry = gui.configure_main_window(root)

        self.assertEqual("1180x760+370+160", geometry)
        root.geometry.assert_called_once_with("1180x760+370+160")
        root.minsize.assert_called_once_with(960, 620)
        root.state.assert_called_once_with("zoomed")

    def test_batch_start_snapshots_tk_values_on_main_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            store = self._store(root_path, api_key="fake-key")
            captured = {}
            finished = threading.Event()

            class FakeRunner:
                def __init__(self, callback):
                    self.running = False

                def run(self, **kwargs):
                    captured.update(kwargs)
                    finished.set()

                def cancel(self):
                    pass

            gui.enable_dpi_awareness()
            root = tk.Tk()
            root.withdraw()
            try:
                app = gui.CaptionApp(root, store)
                app.folder_var.set(str(root_path))
                app.media_mode_var.set("video")
                app.caption_style_var.set("phrases")
                app.subject_filter_var.set("主角")
                app.model_label_var.set(core.MODELS["seed-2.0-pro"].label)
                app.focus_label_var.set("风格 LoRA")
                app.output_language_var.set("en")
                app.trigger_word_var.set("qianyi_style")
                app.concurrency_var.set(10)
                with mock.patch.object(gui, "BatchRunner", FakeRunner):
                    app.start_task()
                    deadline = time.monotonic() + 2
                    while not finished.is_set() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertTrue(finished.is_set())
                self.assertEqual("video", captured["mode"])
                self.assertEqual("phrases", captured["caption_style"])
                self.assertEqual("主角", captured["subject_filter"])
                self.assertEqual("seed-2.0-pro", captured["model_key"])
                self.assertEqual("api", captured["backend"])
                self.assertEqual("style", captured["labeling_focus"])
                self.assertEqual("en", captured["output_language"])
                self.assertEqual("qianyi_style", captured["trigger_word"])
                self.assertEqual(10, captured["concurrency"])
            finally:
                root.update_idletasks()
                app.close()

    def test_local_backend_starts_without_api_key_and_uses_model_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            model_folder = root_path / "model"
            model_folder.mkdir()
            (model_folder / "config.json").write_text("{}", encoding="utf-8")
            image_path = root_path / "image.jpg"
            Image.new("RGB", (32, 32), "white").save(image_path)
            captured = {}
            finished = threading.Event()

            class FakeRunner:
                def __init__(self, callback):
                    self.running = False

                def run(self, **kwargs):
                    captured.update(kwargs)
                    finished.set()

                def cancel(self):
                    pass

            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                app.folder_var.set(str(root_path))
                app.backend_var.set("local")
                app.local_model_var.set(str(model_folder))
                app._backend_changed()
                with mock.patch.object(gui, "BatchRunner", FakeRunner):
                    app.start_task([image_path], force=True)
                    deadline = time.monotonic() + 2
                    while not finished.is_set() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertTrue(finished.is_set())
                self.assertEqual("local", captured["backend"])
                self.assertEqual(str(model_folder), captured["local_model_folder"])
                self.assertEqual(1, captured["concurrency"])
                self.assertEqual("", captured["api_key"])
            finally:
                root.update_idletasks()
                app.close()

    def test_missing_txt_filter_batch_and_trigger_word_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            missing = root_path / "missing.jpg"
            existing = root_path / "existing.jpg"
            Image.new("RGB", (32, 32), "red").save(missing)
            Image.new("RGB", (32, 32), "blue").save(existing)
            core.write_caption(existing, "blue subject")
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                app.folder_var.set(str(root_path))
                app._handle_scan(core.scan_media(root_path, "image"))
                app.filter_var.set("缺少 TXT")
                app.refresh_table()
                self.assertEqual([missing], [item["path"] for item in app.gallery.items])

                with mock.patch.object(app, "start_task") as start:
                    app.process_missing_captions()
                self.assertEqual([missing], start.call_args.args[0])
                self.assertTrue(start.call_args.kwargs["force"])

                core.write_caption(missing, "new caption")
                app._set_item(missing, "success", "new caption")
                self.assertEqual([], app.gallery.items)

                app.filter_var.set("全部状态")
                app.refresh_table()
                app.selected_paths = {str(existing)}
                app.trigger_word_var.set("qianyi_style")
                app.output_language_var.set("en")
                with mock.patch.object(gui.messagebox, "askyesno", return_value=True):
                    app.apply_trigger_to_results()
                    app.apply_trigger_to_results()
                self.assertEqual(
                    "qianyi_style, blue subject",
                    core.caption_path_for(existing).read_text(encoding="utf-8"),
                )
            finally:
                root.update_idletasks()
                app.close()

    def test_orphan_txt_filter_locates_previews_and_deletes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            orphan = root_path / "old-caption.txt"
            orphan.write_text("orphan caption content", encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(
                root, self._store(root_path), show_splash=False
            )
            try:
                app.folder_var.set(str(root_path))
                app._handle_scan(core.scan_media(root_path, "image"))
                self.assertTrue(app.items[str(orphan)]["is_orphan"])
                self.assertIn("孤 1", app.stats_var.get())

                app.filter_var.set("孤立 TXT")
                app._filter_changed()
                self.assertEqual("list", app.view_mode_var.get())
                rows = app.tree.get_children()
                self.assertEqual(1, len(rows))
                self.assertEqual(orphan, app.row_paths[rows[0]])

                app.tree.selection_set(rows[0])
                app.show_selected_item()
                self.assertEqual(
                    tk.DISABLED, str(app.selected_button.cget("state"))
                )
                self.assertIn(
                    "orphan caption content",
                    app.result_text.get("1.0", tk.END),
                )
                with mock.patch.object(gui.subprocess, "Popen") as popen:
                    app.open_selected_location()
                popen.assert_called_once()
                self.assertEqual({str(orphan)}, app.selected_paths)
                with mock.patch.object(gui.messagebox, "showinfo") as showinfo:
                    with mock.patch.object(gui.filedialog, "asksaveasfilename") as save:
                        app.export_results("jsonl")
                showinfo.assert_called_once()
                save.assert_not_called()

                with mock.patch.object(gui.messagebox, "askyesno", return_value=True):
                    app.delete_selected_orphan_captions()
                self.assertFalse(orphan.exists())
                self.assertEqual((), app.tree.get_children())
                self.assertIn("孤 0", app.stats_var.get())
            finally:
                root.update_idletasks()
                app.close()

    def test_gallery_multiselect_force_processes_selected_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            first = root_path / "first.jpg"
            second = root_path / "second.jpg"
            Image.new("RGB", (32, 32), "red").save(first)
            Image.new("RGB", (32, 32), "blue").save(second)
            core.write_caption(first, "existing first")
            core.write_caption(second, "existing second")
            store = self._store(root_path, api_key="fake-key")
            captured = {}
            finished = threading.Event()

            class FakeRunner:
                def __init__(self, callback):
                    self.running = False

                def run(self, **kwargs):
                    captured.update(kwargs)
                    finished.set()

                def cancel(self):
                    pass

            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, store)
            try:
                app.folder_var.set(str(root_path))
                app._handle_scan(core.scan_media(root_path, "image"))
                app._gallery_selected(first, False)
                app._gallery_selected(second, True)
                for _ in range(3):
                    root.update()
                self.assertEqual({str(first), str(second)}, app.selected_paths)
                self.assertEqual("已选 2", app.selection_var.get())
                with mock.patch.object(gui, "BatchRunner", FakeRunner):
                    app.process_selected()
                    deadline = time.monotonic() + 2
                    while not finished.is_set() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertTrue(finished.is_set())
                self.assertFalse(captured["skip_existing"])
                self.assertEqual({first, second}, set(captured["only_paths"]))
            finally:
                root.update_idletasks()
                app.close()

    def test_similarity_analysis_returns_to_filtered_gallery(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            first = root_path / "first.jpg"
            second = root_path / "second.jpg"
            Image.new("RGB", (32, 32), "red").save(first)
            Image.new("RGB", (32, 32), "red").save(second)
            store = self._store(root_path)
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, store)
            try:
                app.folder_var.set(str(root_path))
                app._handle_scan(core.scan_media(root_path, "image"))

                def fake_similarity(paths, threshold, token, progress):
                    progress(len(paths), len(paths))
                    return [[first, second]]

                with mock.patch.object(gui, "find_similar_images", fake_similarity):
                    app.find_similar()
                    deadline = time.monotonic() + 2
                    while app.analysis_token is not None and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertIsNone(app.analysis_token)
                self.assertEqual("相似图", app.filter_var.get())
                self.assertEqual({str(first), str(second)}, app.similar_paths)
                self.assertEqual("normal", str(app.batch_button.cget("state")))
            finally:
                root.update_idletasks()
                app.close()

    def test_project_center_delete_clears_current_workspace_and_recent_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            project = root_path / "project"
            project.mkdir()
            media = project / "keep.jpg"
            Image.new("RGB", (32, 32), "green").save(media)
            store = self._store(root_path)
            settings = store.load()
            settings.update({"last_folder": str(project), "recent_folders": [str(project)]})
            store.save(settings)
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, store, show_splash=False)
            try:
                app.folder_var.set(str(project))
                app._handle_scan(core.scan_media(project, "image"))
                self.assertTrue(app.items)
                with mock.patch.object(gui, "delete_project_metadata", return_value=True) as delete:
                    self.assertTrue(app.delete_project(project, confirm=False))
                delete.assert_called_once_with(project)
                self.assertEqual("", app.folder_var.get())
                self.assertFalse(app.items)
                self.assertNotIn(str(project), app.settings["recent_folders"])
                self.assertTrue(media.is_file())
            finally:
                root.update_idletasks()
                app.close()

    def test_project_center_returns_to_current_project_during_active_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            project = root_path / "project"
            project.mkdir()
            store = self._store(root_path)
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, store, show_splash=False)
            try:
                with mock.patch.object(app, "scan_project"):
                    self.assertTrue(app.open_project(project))
                app.runner = mock.Mock(running=True)

                app.show_project_center()
                selection = app.project_tree.selection()
                self.assertEqual(1, len(selection))
                self.assertEqual(project, app.project_paths[selection[0]])
                self.assertEqual("normal", str(app.return_project_button.cget("state")))

                self.assertTrue(app.return_to_current_project())
                self.assertEqual("pack", app.workspace_frame.winfo_manager())

                app.show_project_center()
                row = app.project_tree.selection()[0]
                event = mock.Mock(y=10)
                with mock.patch.object(app.project_tree, "identify_row", return_value=row):
                    app._project_double_clicked(event)
                self.assertEqual("pack", app.workspace_frame.winfo_manager())
                self.assertEqual(project, app.workspace_project)
            finally:
                if app.runner is not None:
                    app.runner.running = False
                root.update_idletasks()
                app.close()

    def test_stale_scan_events_are_ignored_after_project_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            old_project = root_path / "old"
            new_project = root_path / "new"
            old_project.mkdir()
            new_project.mkdir()
            image = old_project / "old.jpg"
            Image.new("RGB", (32, 32), "red").save(image)
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                app.folder_var.set(str(new_project))
                app.scan_generation = 2
                with mock.patch.object(app, "_handle_scan") as handle_scan:
                    app._post_event(
                        "scan",
                        {
                            "result": core.scan_media(old_project, "image"),
                            "scan_generation": 1,
                        },
                    )
                    app._post_event(
                        "scan_done",
                        {"folder": old_project, "scan_generation": 1},
                    )
                    app._process_events()
                handle_scan.assert_not_called()
                self.assertNotEqual("扫描完成", app.progress_text_var.get())
            finally:
                root.update_idletasks()
                app.close()

    def test_settings_dialog_is_centered_inside_main_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            root = tk.Tk()
            root.geometry("960x620+120+90")
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            dialog = None
            try:
                root.update_idletasks()
                root.update()
                dialog = app.open_settings()
                root.update_idletasks()
                root.update()
                self.assertGreaterEqual(dialog.winfo_rootx(), root.winfo_rootx())
                self.assertGreaterEqual(dialog.winfo_rooty(), root.winfo_rooty())
                self.assertLessEqual(
                    dialog.winfo_rootx() + dialog.winfo_width(),
                    root.winfo_rootx() + root.winfo_width(),
                )
                self.assertLessEqual(
                    dialog.winfo_rooty() + dialog.winfo_height(),
                    root.winfo_rooty() + root.winfo_height(),
                )
                expected_x = root.winfo_rootx() + (root.winfo_width() - dialog.winfo_width()) // 2
                expected_y = root.winfo_rooty() + (root.winfo_height() - dialog.winfo_height()) // 2
                self.assertLessEqual(abs(dialog.winfo_rootx() - expected_x), 2)
                self.assertLessEqual(abs(dialog.winfo_rooty() - expected_y), 2)
            finally:
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
                root.update_idletasks()
                app.close()

    def test_prompt_tab_combines_user_request_with_system_template(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            root = tk.Tk()
            store = self._store(root_path, api_key="fake-key")
            app = gui.CaptionApp(root, store, show_splash=False)
            try:
                app.folder_var.set(str(root_path))
                app.system_prompt_text.delete("1.0", tk.END)
                app.system_prompt_text.insert("1.0", "系统模板")
                app.user_prompt_text.insert("1.0", "保留人物脸部细节")

                validated = app._validate_task()

                self.assertIsNotNone(validated)
                self.assertEqual(
                    "系统模板\n\n## 用户要求\n保留人物脸部细节",
                    validated[1],
                )
                app._save_workspace_settings()
                self.assertEqual("保留人物脸部细节", store.load()["user_prompt"])
            finally:
                root.update_idletasks()
                app.close()

    def test_settings_masks_and_fully_clears_saved_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            root = tk.Tk()
            store = self._store(root_path, api_key="real-secret-key")
            app = gui.CaptionApp(root, store, show_splash=False)
            dialog = None
            try:
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)

                dialog = app.open_settings()
                controls = list(descendants(dialog))
                entry = next(widget for widget in controls if isinstance(widget, ttk.Entry))
                clear = next(
                    widget
                    for widget in controls
                    if isinstance(widget, ttk.Button) and widget.cget("text") == "清除密钥"
                )
                self.assertTrue(entry.get())
                self.assertNotIn("real-secret-key", entry.get())
                self.assertFalse(any(
                    isinstance(widget, ttk.Checkbutton)
                    and "Seed 2.0" in str(widget.cget("text"))
                    for widget in controls
                ))

                with mock.patch.object(gui.messagebox, "askyesno", return_value=True):
                    clear.invoke()

                self.assertEqual("", entry.get())
                self.assertEqual("", store.get_api_key())
                dialog.destroy()
                dialog = app.open_settings()
                controls = list(descendants(dialog))
                entry = next(widget for widget in controls if isinstance(widget, ttk.Entry))
                self.assertEqual("", entry.get())
            finally:
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
                root.update_idletasks()
                app.close()

    def test_launch_asset_resolves_and_smoke_view_skips_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(Path(directory)), show_splash=False)
            try:
                self.assertEqual("芊熠智能打标工作台", gui.APP_TITLE)
                self.assertIn(gui.APP_TITLE, root.title())
                self.assertTrue(gui.resource_path("assets/launch-im-aios.jpg").is_file())
                self.assertTrue(gui.resource_path("assets/launch-qianyi.png").is_file())
                self.assertTrue(gui.resource_path("assets/qianyi-app-icon.png").is_file())
                self.assertTrue(gui.resource_path("assets/qianyi-app.ico").is_file())
                self.assertIsNotNone(app._launch_source)
                self.assertIsNotNone(app._project_banner_source)
                self.assertIsNotNone(app._app_icon_photo)
                self.assertIn(
                    core.MODELS["seed-2.1-pro-turbo"].label,
                    app.model_box["values"],
                )
                banner = app._compose_project_banner(960, 176)
                self.assertIsNotNone(banner)
                self.assertEqual((960, 176), banner.size)
                background = Image.new("RGB", (960, 1), gui.COLORS["bg"])
                self.assertIsNone(
                    ImageChops.difference(
                        banner.crop((0, 175, 960, 176)), background
                    ).getbbox()
                )
                app._render_project_banner()
                banner_text = "\n".join(
                    str(app.project_banner.itemcget(item, "text"))
                    for item in app.project_banner.find_all()
                    if app.project_banner.type(item) == "text"
                )
                self.assertNotIn("MEDIA CAPTION TOOL", banner_text)
                self.assertEqual("", app.launch_frame.winfo_manager())
                self.assertEqual("pack", app.project_center_frame.winfo_manager())
            finally:
                root.update_idletasks()
                app.close()


if __name__ == "__main__":
    unittest.main()
