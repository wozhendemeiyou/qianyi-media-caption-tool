from pathlib import Path
from collections import Counter
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace
import unittest
import sys
from unittest import mock

from PIL import Image, ImageGrab

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

    def test_main_window_uses_expanded_centered_restore_bounds(self):
        root = mock.Mock()
        root.winfo_screenwidth.return_value = 2560
        root.winfo_screenheight.return_value = 1440

        geometry = gui.configure_main_window(root)

        self.assertEqual("1720x1200+420+120", geometry)
        root.geometry.assert_called_once_with("1720x1200+420+120")
        root.minsize.assert_called_once_with(1024, 720)
        root.state.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows taskbar style only exists on Windows")
    def test_windows_root_keeps_taskbar_app_style_through_restore(self):
        import ctypes

        root = tk.Tk()
        try:
            gui.configure_windows_taskbar_identity(root)
            root.update_idletasks()
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            hwnd = int(user32.GetAncestor(root.winfo_id(), 2))
            exstyle = int(user32.GetWindowLongPtrW(hwnd, -20))
            self.assertTrue(exstyle & 0x00040000)  # WS_EX_APPWINDOW
            self.assertFalse(exstyle & 0x00000080)  # WS_EX_TOOLWINDOW
            root.iconify()
            root.update()
            root.deiconify()
            root.state("normal")
            root.update()
            self.assertEqual("normal", root.state())
        finally:
            root.destroy()

    def test_gallery_resize_reflows_existing_cards_without_rebuilding(self):
        root = tk.Tk()
        root.geometry("900x700+40+40")
        gallery = gui.MediaGallery(
            root,
            lambda _path, _extend: None,
            lambda _path: None,
            lambda _path: None,
        )
        gallery.pack(fill=tk.BOTH, expand=True)
        try:
            root.update()
            items = [
                {"path": Path(f"image-{index}.png"), "status": "pending"}
                for index in range(8)
            ]
            gallery.set_items(items, set())
            root.update_idletasks()
            original_cards = dict(gallery.card_frames)
            target_columns = gallery._configured_columns + 1
            target_width = target_columns * 190

            with (
                mock.patch.object(
                    gallery.canvas, "winfo_width", return_value=target_width
                ),
                mock.patch.object(
                    gallery.canvas, "winfo_ismapped", return_value=True
                ),
            ):
                gallery.refresh_layout()

            self.assertEqual(target_columns, gallery._configured_columns)
            self.assertEqual(set(original_cards), set(gallery.card_frames))
            for key, card in original_cards.items():
                self.assertIs(card, gallery.card_frames[key])
                self.assertTrue(card.winfo_exists())
            self.assertEqual(
                1,
                int(gallery.card_frames[str(items[target_columns]["path"])].grid_info()["row"]),
            )

            gallery._canvas_resized(SimpleNamespace(width=target_width + 10))
            self.assertIsNone(gallery._render_after)
            gallery._canvas_resized(SimpleNamespace(width=1))
            self.assertIsNone(gallery._render_after)
        finally:
            root.destroy()

    def test_preview_cache_and_layout_modes_skip_redundant_redraws(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            image_path = root_path / "preview.png"
            Image.new("RGB", (640, 480), "#42637a").save(image_path)
            root = tk.Tk()
            app = gui.CaptionApp(
                root, self._store(root_path), show_splash=False
            )
            try:
                app.show_workspace()
                root.update()
                app.items[str(image_path)] = {
                    "path": image_path,
                    "status": "pending",
                    "is_orphan": False,
                }
                app.selected_paths = {str(image_path)}
                with mock.patch.object(
                    gui, "open_image", wraps=gui.open_image
                ) as open_mock:
                    app._refresh_selected_preview()
                    app._refresh_selected_preview()
                self.assertEqual(1, open_mock.call_count)

                header_width = (
                    1300 if app._workspace_header_layout == "wide" else 1000
                )
                filter_width = 900 if app._filter_bar_layout == "wide" else 620
                with (
                    mock.patch.object(
                        app.workspace_title_block,
                        "grid_forget",
                        wraps=app.workspace_title_block.grid_forget,
                    ) as title_forget,
                    mock.patch.object(
                        app.search_entry,
                        "grid_forget",
                        wraps=app.search_entry.grid_forget,
                    ) as search_forget,
                ):
                    app._layout_workspace_header(header_width)
                    app._layout_filter_bar(filter_width)
                title_forget.assert_not_called()
                search_forget.assert_not_called()

                app._window_unmapped(SimpleNamespace(widget=root))
                self.assertTrue(app._window_suspended)
                app._preview_resized(SimpleNamespace(width=600, height=400))
                self.assertIsNone(app._preview_resize_after)
                app._resume_after_window_map()
                self.assertFalse(app._window_suspended)
            finally:
                root.update_idletasks()
                app.close()

    def test_public_build_starts_with_an_empty_prompt_library(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            try:
                self.assertEqual({}, gui.DEFAULT_PRESETS)
                self.assertEqual({}, app.settings["prompt_presets"])
                self.assertEqual("", app.preset_var.get())
                self.assertFalse(app.preset_box.cget("values"))
                self.assertEqual(
                    "", app.system_prompt_text.get("1.0", tk.END).strip()
                )
            finally:
                root.update_idletasks()
                app.close()

    def test_launch_window_is_compact_centered_and_opaque(self):
        root = mock.Mock()
        root.winfo_screenwidth.return_value = 2560
        root.winfo_screenheight.return_value = 1440
        app = gui.CaptionApp.__new__(gui.CaptionApp)
        app.root = root

        with mock.patch.object(gui.sys, "platform", "win32"):
            app._use_fullscreen_launch_window()

        root.overrideredirect.assert_called_once_with(False)
        self.assertEqual((1440, 820), root.minsize.call_args_list[-1].args)
        self.assertEqual(
            "1440x820+560+310", root.geometry.call_args_list[-1].args[0]
        )
        root.configure.assert_called_once_with(background="#090d1d")
        root.wm_attributes.assert_any_call("-topmost", True)

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
                app._set_system_prompt("单元测试系统提示词")
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
                app._set_system_prompt("单元测试系统提示词")
                app.folder_var.set(str(root_path))
                app.backend_var.set("local")
                app.local_model_var.set(str(model_folder))
                app.concurrency_var.set(4)
                app.enable_mtp_var.set(True)
                app.remove_thinking_tags_var.set(False)
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
                self.assertEqual("huggingface", captured["local_runtime"])
                self.assertEqual(4, captured["concurrency"])
                self.assertEqual("normal", str(app.concurrency_box.cget("state")))
                self.assertEqual("", captured["api_key"])
                self.assertTrue(captured["enable_mtp"])
                self.assertFalse(captured["remove_thinking_tags"])
            finally:
                root.update_idletasks()
                app.close()

    def test_lmstudio_local_backend_does_not_require_huggingface_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
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
                app._set_system_prompt("单元测试系统提示词")
                app.folder_var.set(str(root_path))
                app.backend_var.set("local")
                app.local_runtime_var.set("lmstudio")
                app.local_model_var.set("")
                app.lmstudio_base_url_var.set("http://localhost:1234/v1")
                app.lmstudio_model_var.set("qwen-vl-local")
                app.concurrency_var.set(3)
                app._backend_changed()
                with mock.patch.object(gui, "BatchRunner", FakeRunner):
                    app.start_task([image_path], force=True)
                    deadline = time.monotonic() + 2
                    while not finished.is_set() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)

                self.assertTrue(finished.is_set())
                self.assertEqual("local", captured["backend"])
                self.assertEqual("lmstudio", captured["local_runtime"])
                self.assertEqual(
                    "http://localhost:1234/v1",
                    captured["lmstudio_base_url"],
                )
                self.assertEqual("qwen-vl-local", captured["lmstudio_model"])
                self.assertEqual(3, captured["concurrency"])
                self.assertEqual("", captured["api_key"])
            finally:
                root.update_idletasks()
                app.close()

    def test_lmstudio_platform_refresh_load_and_unload_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            dialog = None
            loaded_instances = []

            def inventory(_endpoint):
                return [{
                    "key": "qwen-vl-local",
                    "display_name": "Qwen VL Local",
                    "format": "gguf",
                    "vision": True,
                    "loaded_instances": list(loaded_instances),
                }]

            def load_model(_endpoint, model_key, *, load_profile):
                self.assertEqual("qwen-vl-local", model_key)
                self.assertEqual("low_vram", load_profile)
                loaded_instances[:] = ["qwen-vl-instance"]
                return {"instance_id": "qwen-vl-instance", "status": "loaded"}

            def unload_model(_endpoint, instance_id):
                self.assertEqual("qwen-vl-instance", instance_id)
                loaded_instances.clear()
                return {"instance_id": instance_id, "status": "unloaded"}

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            def wait_for(predicate):
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    root.update()
                    if predicate():
                        return True
                    time.sleep(0.01)
                return False

            try:
                dialog = app.open_platform_config()
                radios = [
                    widget for widget in descendants(dialog)
                    if isinstance(widget, ttk.Radiobutton)
                ]
                next(
                    widget for widget in radios
                    if widget.cget("text") == "本地模型"
                ).invoke()
                dialog.qianyi_local_runtime_box.set("LM Studio 本地服务")
                dialog.qianyi_local_runtime_box.event_generate(
                    "<<ComboboxSelected>>"
                )
                root.update()

                with mock.patch.object(gui, "list_lmstudio_models", inventory):
                    dialog.qianyi_lmstudio_discover_button.invoke()
                    self.assertTrue(wait_for(
                        lambda: "连接正常" in dialog.qianyi_lmstudio_state_var.get()
                    ))
                    self.assertEqual(
                        ("qwen-vl-local",),
                        tuple(dialog.qianyi_lmstudio_model_box.cget("values")),
                    )
                    self.assertEqual(
                        "readonly",
                        str(dialog.qianyi_lmstudio_model_box.cget("state")),
                    )
                    self.assertEqual(
                        "加载模型", dialog.qianyi_lmstudio_load_button.cget("text")
                    )

                    with mock.patch.object(gui, "load_lmstudio_model", load_model):
                        dialog.qianyi_lmstudio_load_button.invoke()
                        self.assertTrue(wait_for(
                            lambda: "模型已加载" in dialog.qianyi_lmstudio_state_var.get()
                        ))
                    self.assertEqual(
                        "卸载模型", dialog.qianyi_lmstudio_load_button.cget("text")
                    )

                    with mock.patch.object(
                        gui, "unload_lmstudio_model", unload_model
                    ):
                        dialog.qianyi_lmstudio_load_button.invoke()
                        self.assertTrue(wait_for(
                            lambda: "模型已卸载" in dialog.qianyi_lmstudio_state_var.get()
                        ))
                    self.assertEqual(
                        "加载模型", dialog.qianyi_lmstudio_load_button.cget("text")
                    )

                save_button = next(
                    widget for widget in descendants(dialog)
                    if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "保存设置"
                )
                with mock.patch.object(app, "log") as log:
                    save_button.invoke()
                self.assertEqual("local", app.backend_var.get())
                self.assertEqual("lmstudio", app.local_runtime_var.get())
                self.assertEqual("qwen-vl-local", app.lmstudio_model_var.get())
                log.assert_called_once_with(
                    "平台设置已保存：LM Studio / qwen-vl-local"
                )
                dialog = None
            finally:
                if dialog is not None and dialog.winfo_exists():
                    try:
                        dialog.grab_release()
                    except tk.TclError:
                        pass
                    dialog.destroy()
                root.update_idletasks()
                app.close()

    def test_relabel_overwrites_txt_and_refreshes_selected_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            image_path = root_path / "relabel.jpg"
            Image.new("RGB", (32, 32), "white").save(image_path)
            core.write_caption(image_path, "old caption")
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(
                root, self._store(root_path), show_splash=False
            )
            try:
                app.folder_var.set(str(root_path))
                app._handle_scan(core.scan_media(root_path, "image"))
                app.selected_paths = {str(image_path)}
                app._display_selection()
                self.assertEqual(
                    "old caption",
                    app.result_text.get("1.0", tk.END).strip(),
                )
                self.assertIn("已载入", app.result_state_var.get())

                app._set_item(image_path, "running", "正在请求模型")
                self.assertEqual(
                    "old caption",
                    app.result_text.get("1.0", tk.END).strip(),
                )
                self.assertIn("正在生成", app.result_state_var.get())
                self.assertIn(
                    "生成中",
                    app.inspector_tabs.tab(app.result_tab, option="text"),
                )

                core.write_caption(image_path, "new caption")
                app._set_item(
                    image_path,
                    "success",
                    "new caption",
                    elapsed_seconds=1.26,
                )

                self.assertEqual(
                    "new caption",
                    core.caption_path_for(image_path).read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    "new caption",
                    app.result_text.get("1.0", tk.END).strip(),
                )
                self.assertNotIn("old caption", app.result_text.get("1.0", tk.END))
                self.assertIn(
                    gui.STATUS_TEXT["success"], app.selected_item_var.get()
                )
                self.assertIn("已更新", app.result_state_var.get())
                self.assertIn("1.3 秒", app.result_state_var.get())
                self.assertIn(
                    "已更新",
                    app.inspector_tabs.tab(app.result_tab, option="text"),
                )
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
                self.assertEqual({"missing": 1, "invalid": 0}, app.caption_health_counts)
                self.assertIn("缺 1", app.stats_var.get())
                app.filter_var.set("缺少 TXT")
                app.refresh_table()
                self.assertEqual([missing], [item["path"] for item in app.gallery.items])

                with mock.patch.object(app, "start_task") as start:
                    app.process_missing_captions()
                self.assertEqual([missing], start.call_args.args[0])
                self.assertTrue(start.call_args.kwargs["force"])

                core.write_caption(missing, "new caption")
                app._set_item(missing, "success", "new caption")
                self.assertEqual({"missing": 0, "invalid": 0}, app.caption_health_counts)
                self.assertIn("缺 0", app.stats_var.get())
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
                app._set_system_prompt("单元测试系统提示词")
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

    def test_prompt_form_scrollbar_only_moves_the_three_prompt_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.geometry("1080x720+20+20")
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            try:
                app.show_workspace()
                app.right_tabs.select(app.preview_tab)
                app.inspector_tabs.select(app.prompt_tab)
                root.update_idletasks()
                root.update()

                self.assertEqual(
                    "right", app.prompt_form_scrollbar.pack_info()["side"]
                )
                self.assertIs(
                    app.prompt_scroll_host, app.prompt_form_canvas.master
                )
                self.assertIs(
                    app.prompt_form_content, app.prompt_subject_row.master
                )
                self.assertIs(
                    app.prompt_form_content, app.user_prompt_text.frame.master
                )
                self.assertIs(
                    app.prompt_form_content, app.system_prompt_text.frame.master
                )
                self.assertIs(app.prompt_tab, app.preset_box.master.master)

                long_prompt = "\n".join(
                    f"第 {index:03d} 行：完整提示词模板内容"
                    for index in range(1, 301)
                )
                app.settings["prompt_presets"] = {"长模板": long_prompt}
                app.preset_box["values"] = ["长模板"]
                app.preset_var.set("长模板")
                app.apply_preset()
                root.update_idletasks()
                root.update()

                self.assertEqual(
                    long_prompt,
                    app.system_prompt_text.get("1.0", "end-1c"),
                )
                self.assertEqual("1.0", app.system_prompt_text.index(tk.INSERT))
                self.assertEqual(0.0, app.system_prompt_text.yview()[0])
                self.assertEqual(
                    f"完整加载 · {len(long_prompt):,} 字符 · 300 行",
                    app.system_prompt_metric_var.get(),
                )
                outer_before_text_scroll = app.prompt_form_canvas.yview()
                app._prompt_text_mousewheel(
                    SimpleNamespace(delta=-120), app.system_prompt_text
                )
                root.update_idletasks()
                self.assertGreater(app.system_prompt_text.yview()[0], 0.0)
                self.assertEqual(
                    outer_before_text_scroll, app.prompt_form_canvas.yview()
                )
                app.system_prompt_text.yview_moveto(0.0)

                scrollregion = tuple(
                    float(value)
                    for value in str(
                        app.prompt_form_canvas.cget("scrollregion")
                    ).split()
                )
                self.assertGreater(
                    scrollregion[3] - scrollregion[1],
                    app.prompt_form_canvas.winfo_height(),
                )
                toolbar_y = app.preset_box.master.winfo_rooty()
                subject_y = app.prompt_subject_row.winfo_rooty()
                task_settings_view = app.task_settings_canvas.yview()

                app.prompt_form_canvas.yview_moveto(1.0)
                root.update_idletasks()
                root.update()

                self.assertEqual(toolbar_y, app.preset_box.master.winfo_rooty())
                self.assertLess(app.prompt_subject_row.winfo_rooty(), subject_y)
                self.assertEqual(
                    task_settings_view, app.task_settings_canvas.yview()
                )
            finally:
                root.update_idletasks()
                app.close()

    def test_prompt_preset_uses_modern_themed_modal_and_saves_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.geometry("1200x800+40+40")
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            dialog = None
            try:
                app.show_workspace()
                app.system_prompt_text.delete("1.0", tk.END)
                app.system_prompt_text.insert("1.0", "专业人物训练提示词")
                root.update_idletasks()
                root.update()

                with mock.patch.object(gui.simpledialog, "askstring") as legacy:
                    dialog = app.save_preset()
                root.update_idletasks()
                root.update()

                self.assertIsNotNone(dialog)
                legacy.assert_not_called()
                self.assertTrue(bool(dialog.overrideredirect()))
                self.assertEqual("Surface.TFrame", dialog.qianyi_shell.cget("style"))
                self.assertEqual(
                    "Primary.TButton",
                    dialog.qianyi_save_button.cget("style"),
                )
                self.assertEqual("取消", dialog.qianyi_cancel_button.cget("text"))
                self.assertEqual("保存预设", dialog.qianyi_save_button.cget("text"))

                expected_x = (
                    root.winfo_rootx()
                    + (root.winfo_width() - dialog.winfo_width()) // 2
                )
                expected_y = (
                    root.winfo_rooty()
                    + (root.winfo_height() - dialog.winfo_height()) // 2
                )
                self.assertLessEqual(abs(dialog.winfo_rootx() - expected_x), 2)
                self.assertLessEqual(abs(dialog.winfo_rooty() - expected_y), 2)

                dialog.qianyi_save_button.invoke()
                root.update()
                self.assertTrue(dialog.winfo_exists())
                self.assertEqual(
                    "请输入预设名称",
                    dialog.qianyi_feedback_label.cget("text"),
                )

                dialog.qianyi_name_var.set("人物精修")
                root.update()
                dialog.qianyi_save_button.invoke()
                root.update()

                self.assertFalse(dialog.winfo_exists())
                self.assertEqual("人物精修", app.preset_var.get())
                self.assertEqual(
                    "专业人物训练提示词",
                    app.settings["prompt_presets"]["人物精修"],
                )
            finally:
                if dialog is not None:
                    try:
                        if dialog.winfo_exists():
                            dialog.destroy()
                    except tk.TclError:
                        pass
                root.update_idletasks()
                app.close()

    def test_video_navigation_is_prominent_and_switches_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                app.folder_var.set(str(root_path))
                app.show_workspace()
                root.geometry("900x680+80+60")
                root.update_idletasks()
                before = root.geometry()
                with mock.patch.object(app, "_restore_main_window") as restore:
                    with mock.patch.object(app, "scan_project") as scan:
                        app.select_workflow("video")
                        app.show_sampling_panel()
                self.assertEqual("video", app.media_mode_var.get())
                self.assertEqual("视频反推", app.workflow_mode_var.get())
                self.assertEqual("开始视频反推", app.start_button.cget("text"))
                scan.assert_called_once()
                restore.assert_not_called()
                root.update_idletasks()
                self.assertEqual(before, root.geometry())
                self.assertEqual("pack", app.sampling_shell.winfo_manager())
                self.assertTrue(app.sampling_summary_var.get())
                self.assertFalse(app.sampling_expanded)
                self.assertNotIn("sampling", app.nav_buttons)
                self.assertIn("platform", app.nav_buttons)
            finally:
                root.update_idletasks()
                app.close()

    def test_single_reverse_is_independent_and_accepts_upload_drag_and_paste(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            image_path = root_path / "single image.png"
            video_path = root_path / "single video.mp4"
            Image.new("RGB", (24, 24), "#557799").save(image_path)
            video_path.write_bytes(b"video")
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                app.folder_var.set(str(root_path / "unchanged-project"))
                app.media_mode_var.set("video")
                app.show_single_reverse()
                root.update_idletasks()
                self.assertEqual("pack", app.single_reverse_frame.winfo_manager())
                self.assertEqual("", app.workspace_frame.winfo_manager())
                self.assertIn("single", app.nav_buttons)
                self.assertIn("single", app.system_nav_buttons)
                self.assertFalse(hasattr(app, "quick_action_buttons"))
                with (
                    mock.patch.object(
                        gui.filedialog,
                        "askopenfilename",
                        return_value=str(image_path),
                    ),
                ):
                    app.choose_single_image()
                self.assertEqual(image_path.resolve(), app.single_image_path)
                self.assertEqual("normal", str(app.single_image_run_button.cget("state")))
                self.assertEqual("video", app.media_mode_var.get())
                self.assertEqual(str(root_path / "unchanged-project"), app.folder_var.get())

                with mock.patch.object(app, "_load_single_media", return_value=True) as load_media:
                    result = app._single_media_dropped(
                        SimpleNamespace(data=f"{{{video_path}}}")
                    )
                self.assertEqual("break", result)
                self.assertEqual("media", app.single_mode_var.get())
                load_media.assert_called_once_with(video_path.resolve())

                clipboard_image = Image.new("RGB", (18, 12), "#aa8844")
                app._set_single_mode("image")
                with (
                    mock.patch.object(gui.ImageGrab, "grabclipboard", return_value=clipboard_image),
                    mock.patch.object(gui, "app_data_dir", return_value=root_path),
                    mock.patch.object(root, "focus_get", return_value=None),
                ):
                    self.assertEqual("break", app._paste_single_clipboard())
                self.assertTrue(app.single_image_path.is_file())
                self.assertEqual(root_path, app.single_image_path.parents[2])
            finally:
                root.update_idletasks()
                app.close()

    def test_single_media_editor_loads_tracks_and_constrains_manual_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            video_path = root_path / "clip-source.mp4"
            video_path.write_bytes(b"video")

            class FakeWorker:
                def __init__(self, _roots):
                    self.closed = False

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    self.closed = True
                    return None

                def probe(self, _path):
                    return {
                        "duration": 12.5,
                        "video_streams": [{}],
                        "audio_streams": [{}],
                    }

                def extract_frames(self, *_args, **_kwargs):
                    return []

            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                with mock.patch.object(gui, "MediaWorkerController", FakeWorker):
                    app._set_single_mode("media")
                    app._load_single_media(video_path)
                    deadline = time.monotonic() + 2
                    while (
                        not app.single_media_info
                        and time.monotonic() < deadline
                    ):
                        root.update()
                        time.sleep(0.01)

                self.assertTrue(app.single_media_info)
                self.assertAlmostEqual(12.5, app.single_clip_end_var.get(), places=2)
                self.assertEqual("normal", str(app.single_clip_reverse_button.cget("state")))
                self.assertEqual("normal", str(app.single_audio_check.cget("state")))
                app.single_clip_start_var.set(12.4)
                app._single_clip_changed("start")
                self.assertGreaterEqual(
                    app.single_clip_end_var.get() - app.single_clip_start_var.get(),
                    0.25,
                )
                app.single_clip_start_var.set(2.0)
                app.single_clip_end_var.set(7.25)
                app._single_clip_changed("end")
                self.assertAlmostEqual(5.25, app.single_clip_end_var.get() - app.single_clip_start_var.get())
            finally:
                root.update_idletasks()
                app.close()

    def test_single_clip_routes_generated_media_without_mutating_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            video_path = root_path / "source.mp4"
            video_path.write_bytes(b"video")

            class FakeWorker:
                def __init__(self, _roots):
                    self.closed = False

                def trim_video(
                    self,
                    _source,
                    output_path,
                    start_seconds,
                    end_seconds,
                    include_audio=True,
                ):
                    self.assert_selection = (start_seconds, end_seconds, include_audio)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(b"clip")
                    return output_path

                def close(self):
                    self.closed = True

            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, self._store(root_path), show_splash=False)
            try:
                original_project = root_path / "project-remains"
                app.folder_var.set(str(original_project))
                app.media_mode_var.set("image")
                with (
                    mock.patch.object(gui, "MediaWorkerController", FakeWorker),
                    mock.patch.object(app, "start_task") as start_task,
                ):
                    app.start_single_clip_task(
                        video_path,
                        1.5,
                        4.0,
                        include_audio=False,
                        action="reverse",
                    )
                    deadline = time.monotonic() + 2
                    while not start_task.called and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)

                self.assertTrue(start_task.called)
                clip_paths = start_task.call_args.args[0]
                self.assertEqual(1, len(clip_paths))
                self.assertTrue(clip_paths[0].is_file())
                self.assertIn("片段反推", str(clip_paths[0].parent))
                self.assertEqual("image", app.media_mode_var.get())
                self.assertEqual(str(original_project), app.folder_var.get())
                start_task.assert_called_once_with(
                    clip_paths,
                    force=True,
                    context="single",
                    folder_override=clip_paths[0].parent,
                    mode_override="video",
                )
            finally:
                root.update_idletasks()
                app.close()

    def test_provider_and_sampling_snapshot_reaches_batch_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            store = self._store(root_path)
            store.set_api_key("qwen-key", "qwen")
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
            app = gui.CaptionApp(root, store, show_splash=False)
            try:
                app._set_system_prompt("单元测试系统提示词")
                app.folder_var.set(str(root_path))
                app.provider_label_var.set(core.API_PROVIDERS["qwen"].label)
                app._provider_changed()
                app.model_label_var.set("qwen3.8-max")
                app.max_tokens_var.set(1400)
                app.temperature_var.set(0.55)
                app.top_p_var.set(0.75)
                app.top_k_var.set(32)
                app.seed_var.set("123")
                app.remove_thinking_tags_var.set(True)
                with mock.patch.object(gui, "BatchRunner", FakeRunner):
                    app.start_task()
                    deadline = time.monotonic() + 2
                    while not finished.is_set() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                self.assertTrue(finished.is_set())
                self.assertEqual("qwen", captured["provider_key"])
                self.assertEqual("qwen3.8-max", captured["api_model"])
                self.assertEqual(
                    core.API_PROVIDERS["qwen"].chat_url,
                    captured["api_endpoint"],
                )
                self.assertEqual(1400, captured["sampling"]["max_tokens"])
                self.assertEqual(0.55, captured["sampling"]["temperature"])
                self.assertEqual(32, captured["sampling"]["top_k"])
                self.assertEqual(123, captured["sampling"]["seed"])
                self.assertTrue(captured["remove_thinking_tags"])
            finally:
                root.update_idletasks()
                app.close()

    def test_single_runner_events_update_only_standalone_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            project = root_path / "batch-project"
            project.mkdir()
            image_path = root_path / "single.png"
            Image.new("RGB", (32, 24), "#557799").save(image_path)
            store = self._store(root_path, api_key="test-key")
            captured = {}
            finished = threading.Event()

            class FakeRunner:
                def __init__(self, callback):
                    self.callback = callback
                    self.running = False

                def run(self, **kwargs):
                    captured.update(kwargs)
                    path = kwargs["only_paths"][0]
                    self.callback("scan", {"result": core.ScanResult(files=[path])})
                    self.callback(
                        "status",
                        {"path": path, "status": "running", "detail": "正在请求模型"},
                    )
                    self.callback(
                        "status",
                        {
                            "path": path,
                            "status": "success",
                            "detail": "一张测试图片",
                            "elapsed_seconds": 2.0,
                            "character_count": 7,
                            "characters_per_second": 3.5,
                        },
                    )
                    self.callback("progress", {"completed": 1, "total": 1, "eta": 0})
                    self.callback(
                        "done",
                        {
                            "status": "completed",
                            "summary": core.BatchSummary(
                                total=1,
                                success=1,
                                characters=7,
                                elapsed_seconds=2.0,
                            ),
                            "journal_dir": root_path,
                        },
                    )
                    finished.set()

                def cancel(self):
                    pass

            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(root, store, show_splash=False)
            try:
                app._set_system_prompt("单元测试系统提示词")
                app.folder_var.set(str(project))
                app.media_mode_var.set("video")
                app.single_task_kind = "image"
                app.single_task_path = image_path
                with mock.patch.object(gui, "BatchRunner", FakeRunner):
                    app.start_task(
                        [image_path],
                        force=True,
                        context="single",
                        folder_override=image_path.parent,
                        mode_override="image",
                    )
                    deadline = time.monotonic() + 2
                    while (
                        app.single_image_result_text.get("1.0", "end-1c").strip()
                        != "一张测试图片"
                        and time.monotonic() < deadline
                    ):
                        root.update()
                        time.sleep(0.01)

                self.assertTrue(finished.is_set())
                self.assertFalse(captured["write_output"])
                self.assertEqual(str(project), app.folder_var.get())
                self.assertEqual("video", app.media_mode_var.get())
                self.assertFalse(app.items)
                self.assertEqual(
                    "一张测试图片",
                    app.single_image_result_text.get("1.0", "end-1c").strip(),
                )
                self.assertIn("耗时 2.0 秒", app.single_metrics_var.get())
                self.assertEqual(100, app.single_progress_var.get())
            finally:
                root.update_idletasks()
                app.close()

    def test_platform_config_uses_public_providers_and_built_in_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            dialog = None
            try:
                app.provider_label_var.set(core.API_PROVIDERS["openai"].label)
                app._provider_changed()
                self.assertEqual("readonly", str(app.model_box.cget("state")))
                self.assertIn("gpt-5.6-sol", app.model_box.cget("values"))
                self.assertIn("gpt-5.5", app.model_box.cget("values"))
                self.assertIn("gpt-4.1", app.model_box.cget("values"))
                self.assertEqual("readonly", str(app.endpoint_box.cget("state")))
                self.assertEqual(
                    core.API_PROVIDERS["openai"].chat_url,
                    app.custom_endpoint_var.get(),
                )
                self.assertEqual("", app.endpoint_box.winfo_manager())

                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)

                dialog = app.open_platform_config()
                controls = list(descendants(dialog))
                self.assertEqual("平台设置", dialog.title())
                provider_menu = dialog.qianyi_provider_menu
                provider_labels = [
                    str(provider_menu.entrycget(index, "label")).strip()
                    for index in range(provider_menu.index("end") + 1)
                ]
                for provider_key in (
                    "volcengine", "openai", "google", "moonshot", "qwen",
                    "siliconflow", "custom",
                ):
                    self.assertIn(
                        core.API_PROVIDERS[provider_key].label,
                        provider_labels,
                    )
                self.assertTrue(all(
                    provider_menu.entrycget(index, "image")
                    for index in range(provider_menu.index("end") + 1)
                ))
                self.assertNotIn("MiniMax", provider_labels)
                self.assertNotIn("OpenRouter", provider_labels)
                self.assertEqual(
                    {*gui.PUBLIC_PROVIDER_KEYS, "connection"},
                    set(app.provider_icons),
                )
                labels = [
                    str(widget.cget("text"))
                    for widget in controls
                    if isinstance(widget, ttk.Label)
                ]
                for label in ("运行后端", "本地模型目录"):
                    self.assertIn(label, labels)
                self.assertNotIn("输出格式", labels)
                self.assertNotIn("输出语言", labels)
                self.assertFalse(any(
                    isinstance(widget, ttk.LabelFrame)
                    and widget.cget("text") == "输出偏好"
                    for widget in controls
                ))
                self.assertFalse(any(
                    isinstance(widget, ttk.Label)
                    and "两种后端互斥" in str(widget.cget("text"))
                    for widget in controls
                ))
                self.assertEqual(
                    "", dialog.qianyi_local_concurrency_note.winfo_manager()
                )
                self.assertIn("Base URL", labels)
                self.assertIn("系统已内置", dialog.qianyi_route_note_var.get())
                self.assertEqual("", dialog.qianyi_route_label.winfo_manager())
                self.assertEqual("", dialog.qianyi_route_box.winfo_manager())
                self.assertFalse(dialog.qianyi_mtp_switch.enabled)
                self.assertTrue(dialog.qianyi_thinking_switch.enabled)
                self.assertEqual(
                    "启用 MTP", dialog.qianyi_mtp_switch.label.cget("text")
                )
                self.assertEqual(
                    "移除思考标签",
                    dialog.qianyi_thinking_switch.label.cget("text"),
                )
                self.assertFalse(any(
                    isinstance(widget, ttk.Checkbutton)
                    and "GitHub 版本更新" in str(widget.cget("text"))
                    for widget in controls
                ))
                self.assertEqual(
                    "启动后自动检查 GitHub 版本更新",
                    app.system_auto_update_check.cget("text"),
                )
                workspace_labels = [
                    str(widget.cget("text"))
                    for widget in descendants(app.workspace_frame)
                    if isinstance(widget, ttk.Label)
                    and widget.winfo_manager()
                ]
                for moved_label in ("输出", "后端", "语言", "本地模型目录"):
                    self.assertNotIn(moved_label, workspace_labels)
                portal = next(
                    widget
                    for widget in controls
                    if isinstance(widget, ttk.Button)
                    and "前往" in str(widget.cget("text"))
                )
                with mock.patch.object(gui.webbrowser, "open", return_value=True) as opened:
                    portal.invoke()
                opened.assert_called_once_with(
                    gui.API_KEY_PORTALS["openai"][1], new=2
                )

                dialog.update_idletasks()
                provider_geometry = (
                    dialog.qianyi_provider_button.winfo_x(),
                    dialog.qianyi_provider_button.winfo_y(),
                    dialog.qianyi_provider_button.winfo_width(),
                )
                builtin_dialog_height = dialog.winfo_height()

                provider_menu.invoke(
                    provider_labels.index(core.API_PROVIDERS["custom"].label)
                )
                dialog.update()
                self.assertEqual("custom", dialog.qianyi_provider_key)
                self.assertEqual(
                    provider_geometry,
                    (
                        dialog.qianyi_provider_button.winfo_x(),
                        dialog.qianyi_provider_button.winfo_y(),
                        dialog.qianyi_provider_button.winfo_width(),
                    ),
                )
                self.assertGreater(dialog.winfo_height(), builtin_dialog_height)
                self.assertGreaterEqual(
                    dialog.winfo_height(), dialog.winfo_reqheight()
                )
                self.assertEqual("normal", str(dialog.qianyi_model_box.cget("state")))
                self.assertEqual("normal", str(dialog.qianyi_route_box.cget("state")))
                self.assertEqual("grid", dialog.qianyi_route_label.winfo_manager())
                self.assertEqual("grid", dialog.qianyi_route_box.winfo_manager())
                self.assertIn(
                    "请填写 OpenAI 兼容 Base URL",
                    dialog.qianyi_route_note_var.get(),
                )
                dialog.qianyi_route_box.set("https://custom.example/v1")
                dialog.qianyi_model_box.set("vision-model-current")
                provider_menu.invoke(
                    provider_labels.index(core.API_PROVIDERS["openai"].label)
                )
                dialog.update()
                self.assertEqual(provider_geometry, (
                    dialog.qianyi_provider_button.winfo_x(),
                    dialog.qianyi_provider_button.winfo_y(),
                    dialog.qianyi_provider_button.winfo_width(),
                ))
                self.assertEqual(builtin_dialog_height, dialog.winfo_height())
                self.assertEqual("", dialog.qianyi_route_box.winfo_manager())

                radio_buttons = [
                    widget for widget in controls
                    if isinstance(widget, ttk.Radiobutton)
                ]
                next(
                    widget for widget in radio_buttons
                    if widget.cget("text") == "本地模型"
                ).invoke()
                dialog.update()
                local_entry = dialog.qianyi_local_model_entry
                self.assertEqual("normal", str(local_entry.cget("state")))
                self.assertEqual(
                    "pack", dialog.qianyi_local_concurrency_note.winfo_manager()
                )
                self.assertIn(
                    "建议从 1 开始",
                    dialog.qianyi_local_concurrency_note.cget("text"),
                )
                self.assertEqual(
                    "readonly",
                    str(dialog.qianyi_local_runtime_box.cget("state")),
                )
                dialog.qianyi_local_runtime_box.set("LM Studio 本地服务")
                dialog.qianyi_local_runtime_box.event_generate(
                    "<<ComboboxSelected>>"
                )
                dialog.update()
                self.assertEqual("disabled", str(local_entry.cget("state")))
                self.assertEqual(
                    "normal",
                    str(dialog.qianyi_lmstudio_url_entry.cget("state")),
                )
                self.assertEqual(
                    "readonly",
                    str(dialog.qianyi_lmstudio_model_box.cget("state")),
                )
                self.assertEqual(
                    "readonly",
                    str(dialog.qianyi_lmstudio_profile_box.cget("state")),
                )
                self.assertEqual(
                    "disabled",
                    str(dialog.qianyi_lmstudio_load_button.cget("state")),
                )
                self.assertFalse(dialog.qianyi_mtp_switch.enabled)
                dialog.qianyi_local_runtime_box.set(
                    "Hugging Face 本地目录"
                )
                dialog.qianyi_local_runtime_box.event_generate(
                    "<<ComboboxSelected>>"
                )
                dialog.update()
                self.assertEqual("normal", str(local_entry.cget("state")))
                self.assertTrue(dialog.qianyi_mtp_switch.enabled)
                self.assertEqual(
                    "disabled", str(dialog.qianyi_provider_button.cget("state"))
                )
                self.assertEqual(
                    "disabled", str(dialog.qianyi_model_box.cget("state"))
                )
                self.assertEqual(
                    "disabled", str(dialog.qianyi_route_box.cget("state"))
                )
                self.assertEqual(
                    "disabled", str(dialog.qianyi_api_key_entry.cget("state"))
                )
                next(
                    widget for widget in radio_buttons
                    if widget.cget("text") == "外部 API"
                ).invoke()
                dialog.update()
                self.assertEqual("disabled", str(local_entry.cget("state")))
                self.assertEqual(
                    "", dialog.qianyi_local_concurrency_note.winfo_manager()
                )
                self.assertEqual(
                    "normal", str(dialog.qianyi_provider_button.cget("state"))
                )
                self.assertEqual("readonly", str(dialog.qianyi_model_box.cget("state")))
                next(
                    widget for widget in radio_buttons
                    if widget.cget("text") == "本地模型"
                ).invoke()
                local_entry.insert(0, r"D:\Models\Vision")
                save_button = next(
                    widget for widget in controls
                    if isinstance(widget, ttk.Button)
                    and widget.cget("text") == "保存设置"
                )
                with mock.patch.object(app, "log") as log:
                    save_button.invoke()
                self.assertEqual("natural", app.caption_style_var.get())
                self.assertEqual("zh", app.output_language_var.get())
                self.assertEqual("local", app.backend_var.get())
                self.assertEqual(r"D:\Models\Vision", app.local_model_var.get())
                self.assertEqual(
                    r"D:\Models\Vision", app.settings["local_model_folder"]
                )
                self.assertEqual(
                    "vision-model-current", app.settings["api_models"]["custom"]
                )
                self.assertEqual(
                    "https://custom.example/v1",
                    app.settings["api_endpoints"]["custom"],
                )
                log.assert_called_once_with(
                    "平台设置已保存：Hugging Face 本地模型 / Vision"
                )
                dialog = None
            finally:
                if dialog is not None and dialog.winfo_exists():
                    dialog.destroy()
                root.update_idletasks()
                app.close()

    def test_hardware_monitor_formats_nonblocking_sample(self):
        monitor = gui.HardwareMonitor(interval=0.5)
        with mock.patch.object(monitor, "_cpu_percent", return_value=23.4):
            with mock.patch.object(
                monitor,
                "_memory_sample",
                return_value={"percent": 50.0, "used_gb": 8.0, "total_gb": 16.0},
            ):
                with mock.patch.object(
                    monitor,
                    "_query_gpu",
                    return_value={
                        "percent": 40.0,
                        "memory_used_mb": 1024.0,
                        "memory_total_mb": 8192.0,
                        "temperature_c": 46.0,
                    },
                ):
                    value = monitor.sample()
        self.assertEqual(23.4, value["cpu_percent"])
        self.assertEqual(50.0, value["memory"]["percent"])
        self.assertEqual(40.0, value["gpu"]["percent"])
        self.assertEqual(46.0, value["gpu"]["temperature_c"])

    def test_generation_metrics_and_compact_hardware_footer(self):
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            root = tk.Tk()
            app = gui.CaptionApp(
                root, self._store(root_path), show_splash=False
            )
            try:
                app.show_workspace()
                app._apply_hardware_sample({
                    "cpu_percent": 25.0,
                    "cpu_temperature_c": None,
                    "memory": {
                        "percent": 50.0,
                        "used_gb": 16.0,
                        "total_gb": 32.0,
                    },
                    "gpu": {
                        "percent": 40.0,
                        "memory_used_mb": 4096.0,
                        "memory_total_mb": 8192.0,
                        "temperature_c": 46.0,
                    },
                })
                self.assertEqual(25.0, app.cpu_percent_var.get())
                self.assertEqual("25%", app.cpu_metric_var.get())
                self.assertEqual("16.0/32G", app.memory_metric_var.get())
                self.assertEqual("4.0/8G", app.vram_metric_var.get())
                self.assertEqual("46°C", app.gpu_temperature_metric_var.get())
                self.assertNotIn(
                    "待机",
                    " ".join(
                        str(widget.cget("text"))
                        for widget in app.status_host.winfo_children()
                        if isinstance(widget, ttk.Label)
                    ),
                )
                self.assertEqual(
                    " · 耗时 2.0 秒 · 字数 120 · 速度 60.0 字/秒",
                    app._format_generation_metrics(
                        "success",
                        elapsed_seconds=2.0,
                        character_count=120,
                        characters_per_second=60.0,
                    ),
                )
                image_path = root_path / "metric.jpg"
                Image.new("RGB", (24, 24), "white").save(image_path)
                app.folder_var.set(str(root_path))
                app._set_item(
                    image_path,
                    "pending",
                    "",
                    caption_exists=False,
                    caption_usable=False,
                )
                with mock.patch.object(app, "log") as log:
                    app._post_event("status", {
                        "path": image_path,
                        "status": "success",
                        "detail": "中文 test",
                        "elapsed_seconds": 2.0,
                        "character_count": 6,
                        "characters_per_second": 3.0,
                    })
                    app._process_events()
                self.assertTrue(any(
                    "耗时 2.0 秒 · 字数 6 · 速度 3.0 字/秒"
                    in str(call.args[0])
                    for call in log.call_args_list
                ))

                summary = core.BatchSummary(
                    total=1,
                    success=1,
                    characters=120,
                    elapsed_seconds=2.0,
                )
                with (
                    mock.patch.object(app, "log") as log,
                    mock.patch.object(app, "refresh_project_center"),
                ):
                    app._handle_done("completed", summary, root_path)
                self.assertIn(
                    "总耗时 2.0 秒，总字数 120，平均速度 60.0 字/秒",
                    log.call_args.args[0],
                )
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
                entry = next(
                    widget
                    for widget in controls
                    if type(widget) is ttk.Entry and widget.cget("show") == "•"
                )
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
                entry = next(
                    widget
                    for widget in controls
                    if type(widget) is ttk.Entry and widget.cget("show") == "•"
                )
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
                self.assertTrue(gui.resource_path("assets/launch-qianyi.png").is_file())
                self.assertTrue(gui.resource_path("assets/qianyi-app-icon.png").is_file())
                self.assertTrue(gui.resource_path("assets/qianyi-app.ico").is_file())
                for theme_key in ("night", "day"):
                    for icon_key in (
                        "project", "image", "video", "platform", "system",
                        "night", "day"
                    ):
                        icon_path = gui.resource_path(
                            f"assets/nav-icons/{theme_key}/{icon_key}.png"
                        )
                        self.assertTrue(icon_path.is_file())
                        with Image.open(icon_path) as icon:
                            self.assertEqual("RGBA", icon.mode)
                            self.assertEqual((30, 30), icon.size)
                            self.assertEqual((0, 255), icon.getchannel("A").getextrema())
                for provider_key in (*gui.PUBLIC_PROVIDER_KEYS, "connection"):
                    icon_path = gui.resource_path(
                        f"assets/provider-icons/{provider_key}.png"
                    )
                    self.assertTrue(icon_path.is_file())
                    with Image.open(icon_path) as icon:
                        self.assertEqual((32, 32), icon.size)
                self.assertIsNotNone(app._app_icon_photo)
                self.assertEqual("#090d1d", app.launch_frame.cget("background"))
                self.assertEqual((640, 360), app._compose_launch_background(640, 360).size)
                self.assertEqual(
                    ("正在加载视觉工作台", "正在加载界面与视觉资源"),
                    app._launch_status_copy(),
                )
                app.launch_progress = 80
                self.assertEqual(
                    ("工作台即将就绪", "正在完成最后的启动检查"),
                    app._launch_status_copy(),
                )
                app._render_launch(SimpleNamespace(width=1180, height=680))
                launch_text = "\n".join(
                    str(app.launch_canvas.itemcget(item, "text"))
                    for item in app.launch_canvas.find_all()
                    if app.launch_canvas.type(item) == "text"
                )
                self.assertIn(gui.APP_TITLE, launch_text)
                self.assertIn("工作台即将就绪", launch_text)
                self.assertIn(
                    core.MODELS["seed-2.1-pro-turbo"].label,
                    app.model_box["values"],
                )
                self.assertFalse(hasattr(app, "project_banner"))
                self.assertEqual("night", app.theme_key)
                self.assertEqual(gui.THEMES["night"]["bg"], gui.COLORS["bg"])
                project_center_buttons = [
                    str(widget.cget("text"))
                    for widget in app.project_center_frame.winfo_children()[0].winfo_children()
                    for widget in widget.winfo_children()
                    if isinstance(widget, ttk.Button)
                ]
                self.assertNotIn("平台设置", project_center_buttons)
                self.assertNotIn("系统说明", project_center_buttons)
                self.assertNotIn("单次反推", project_center_buttons)
                self.assertEqual("项目中心", app.nav_buttons["project"].cget("text"))
                self.assertEqual("系统说明", app.nav_buttons["system"].cget("text"))
                self.assertIs(app.workspace_nav, app.nav_buttons["project"].master)
                self.assertEqual("left", app.workspace_nav.pack_info()["side"])
                self.assertEqual(
                    ["当前素材", "任务设置"],
                    [app.right_tabs.tab(tab, "text") for tab in app.right_tabs.tabs()],
                )
                self.assertEqual(
                    ["标注结果", "提示词", "运行日志"],
                    [
                        app.inspector_tabs.tab(tab, "text")
                        for tab in app.inspector_tabs.tabs()
                    ],
                )
                self.assertFalse(app.sampling_expanded)
                self.assertEqual("pack", app.sampling_shell.winfo_manager())
                self.assertTrue(app.sampling_save_button.winfo_exists())
                self.assertTrue(app.seed_random_button.winfo_exists())
                app._layout_filter_bar(620)
                self.assertEqual(1, app.filter_box.grid_info()["row"])
                self.assertEqual(0, app.search_entry.grid_info()["row"])
                app.show_system_info()
                self.assertEqual("pack", app.system_info_frame.winfo_manager())
                topbar_buttons = [
                    str(widget.cget("text"))
                    for widget in app.system_info_frame.winfo_children()[0].winfo_children()
                    for widget in widget.winfo_children()
                    if isinstance(widget, ttk.Button)
                ]
                self.assertNotIn("检查更新", topbar_buttons)
                self.assertIn(f"v{core.APP_VERSION}", app.system_update_state_var.get())
                app._apply_release_to_system_info({
                    "tag": "v3.4",
                    "notes": "旧版本说明",
                    "published_at": "2026-08-08T03:07:26Z",
                    "is_newer": False,
                })
                self.assertIn(
                    gui.RELEASE_NOTES[0],
                    app.system_release_notes.get("1.0", tk.END),
                )
                self.assertNotIn(
                    "旧版本说明", app.system_release_notes.get("1.0", tk.END)
                )
                self.assertEqual("", app.system_release_date_var.get())
                app._show_update_banner({
                    "tag": "v99.0",
                    "url": "https://github.com/example/releases/tag/v99.0",
                    "notes": "测试更新",
                    "is_newer": True,
                })
                self.assertTrue(app.update_banner_visible)
                self.assertIn("v99.0", app.update_banner_var.get())
                app.dismiss_update_banner()
                self.assertFalse(app.update_banner_visible)
                app.show_workspace()
                self.assertEqual(
                    (str(app.toolbar_icons["night"]["image"]),),
                    app.nav_buttons["image"].cget("image"),
                )
                app._set_theme("day")
                self.assertEqual("day", app.theme_key)
                self.assertEqual("day", app.settings["theme"])
                self.assertEqual(gui.THEMES["day"]["bg"], gui.COLORS["bg"])
                self.assertEqual(
                    (str(app.toolbar_icons["day"]["image"]),),
                    app.nav_buttons["image"].cget("image"),
                )
                self.assertEqual(
                    gui.COLORS["media_bg"], app.preview_label.cget("background")
                )
                for text_widget in (
                    app.result_text,
                    app.user_prompt_text,
                    app.system_prompt_text,
                    app.log_text,
                    app.single_image_result_text,
                    app.single_media_result_text,
                ):
                    self.assertEqual(
                        gui.THEMES["day"]["input_bg"],
                        text_widget.cget("background"),
                    )
                    self.assertEqual(
                        gui.THEMES["day"]["input_border"],
                        text_widget.cget("highlightbackground"),
                    )
                self.assertEqual(
                    gui.THEMES["day"]["input_readonly"],
                    app.system_release_notes.cget("background"),
                )
                self.assertEqual(
                    "disabled", str(app.system_release_notes.cget("state"))
                )
                app._set_release_notes("主题回归测试")
                self.assertEqual(
                    gui.THEMES["day"]["input_readonly"],
                    app.system_release_notes.cget("background"),
                )
                for theme_key in ("night", "day", "night", "day"):
                    app._set_theme(theme_key)
                    root.update_idletasks()
                    for text_widget in (
                        app.result_text,
                        app.user_prompt_text,
                        app.system_prompt_text,
                        app.log_text,
                        app.single_image_result_text,
                        app.single_media_result_text,
                    ):
                        self.assertEqual(
                            gui.THEMES[theme_key]["input_bg"],
                            text_widget.cget("background"),
                        )
                    self.assertEqual(
                        gui.THEMES[theme_key]["input_readonly"],
                        app.system_release_notes.cget("background"),
                    )
                    for canvas, color_key in (
                        (app.single_image_canvas, "media_bg"),
                        (app.single_media_canvas, "media_bg"),
                        (app.single_editor_preview_canvas, "media_bg"),
                        (app.single_timeline_canvas, "input_readonly"),
                    ):
                        self.assertEqual(
                            gui.THEMES[theme_key][color_key],
                            canvas.cget("background"),
                        )
                self.assertEqual(7, len(app._themed_text_widgets))
                app.user_prompt_text.configure(
                    background=gui.THEMES["night"]["input_bg"]
                )
                app.system_prompt_text.configure(
                    background=gui.THEMES["night"]["input_bg"]
                )
                app.log_text.configure(
                    background=gui.THEMES["night"]["input_bg"]
                )
                # Selecting the already active theme must repair stale native colors.
                app._set_theme("day")
                for text_widget in (
                    app.user_prompt_text,
                    app.system_prompt_text,
                    app.log_text,
                ):
                    self.assertEqual(
                        gui.THEMES["day"]["input_bg"],
                        text_widget.cget("background"),
                    )
                for index in range(12):
                    theme_key = "night" if index % 2 == 0 else "day"
                    app.inspector_tabs.select(
                        app.result_tab if index % 3 else app.prompt_tab
                    )
                    app._set_theme(theme_key)
                    app.user_prompt_text.configure(background="#010203")
                    app.system_prompt_text.configure(background="#010203")
                    app.log_text.configure(background="#010203")
                    app._schedule_theme_sync()
                    deadline = time.monotonic() + 0.25
                    while time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    for text_widget in (
                        app.result_text,
                        app.user_prompt_text,
                        app.system_prompt_text,
                        app.log_text,
                    ):
                        self.assertEqual(
                            gui.THEMES[theme_key]["input_bg"],
                            text_widget.cget("background"),
                        )
                    self.assertEqual(
                        gui.THEMES[theme_key]["input_readonly"],
                        app.system_release_notes.cget("background"),
                    )
                app._set_theme("day")
                style = ttk.Style(root)
                self.assertEqual(
                    gui.THEMES["day"]["input_bg"],
                    style.lookup("TEntry", "fieldbackground"),
                )
                self.assertEqual(
                    gui.THEMES["day"]["input_readonly"],
                    style.lookup("TCombobox", "fieldbackground", ("readonly",)),
                )
                self.assertIn(
                    "RoundedForm.field", style.layout("TEntry")[0][0]
                )
                self.assertIn(
                    "RoundedForm.field", style.layout("TCombobox")[0][0]
                )
                self.assertIn(
                    "RoundedForm.field", style.layout("TSpinbox")[0][0]
                )
                self.assertTrue(
                    all(
                        button.cget("style") == "ThemeActive.TButton"
                        for button in app.theme_buttons["day"]
                    )
                )
                tooltip = app.nav_tooltips[2]
                tooltip.show()
                tooltip_label = tooltip.window.winfo_children()[0]
                self.assertEqual(
                    gui.COLORS["surface_alt"], tooltip_label.cget("background")
                )
                self.assertEqual(
                    gui.COLORS["text"], tooltip_label.cget("foreground")
                )
                tooltip.hide()
                app.show_project_center()
                self.assertEqual("", app.launch_frame.winfo_manager())
                self.assertEqual("pack", app.project_center_frame.winfo_manager())
            finally:
                root.update_idletasks()
                app.close()

    def test_cached_combobox_popdowns_and_menus_follow_theme_roundtrips(self):
        def popdown_paths(combobox: ttk.Combobox) -> tuple[str, str, str]:
            popdown = str(
                combobox.tk.call(
                    "ttk::combobox::PopdownWindow", combobox._w
                )
            )
            return popdown, f"{popdown}.f.l", f"{popdown}.f.sb"

        def assert_popdown_palette(
            combobox: ttk.Combobox, palette: dict[str, str]
        ) -> None:
            popdown, listbox, scrollbar = popdown_paths(combobox)
            self.assertEqual(
                palette["input_border"],
                combobox.tk.call(popdown, "cget", "-background"),
            )
            expected = {
                "-background": palette["input_bg"],
                "-foreground": palette["text"],
                "-selectbackground": palette["selection"],
                "-selectforeground": palette["text"],
                "-disabledforeground": palette["disabled_fg"],
                "-highlightbackground": palette["input_border"],
                "-highlightcolor": palette["input_focus"],
            }
            for option, value in expected.items():
                self.assertEqual(
                    value,
                    combobox.tk.call(listbox, "cget", option),
                    f"{combobox} {option}",
                )
            self.assertEqual(
                "TScrollbar",
                combobox.tk.call(scrollbar, "cget", "-style"),
            )

        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            dialog = None
            try:
                base_comboboxes = [
                    widget
                    for widget in app._walk_widget_tree()
                    if isinstance(widget, ttk.Combobox)
                ]
                self.assertGreaterEqual(len(base_comboboxes), 8)

                for theme_key in ("night", "day", "night", "day"):
                    stale_key = "day" if theme_key == "night" else "night"
                    stale = gui.THEMES[stale_key]
                    for combobox in base_comboboxes:
                        popdown, listbox, _scrollbar = popdown_paths(combobox)
                        combobox.tk.call(
                            popdown,
                            "configure",
                            "-background",
                            stale["input_border"],
                        )
                        combobox.tk.call(
                            listbox,
                            "configure",
                            "-background",
                            stale["input_bg"],
                            "-foreground",
                            stale["text"],
                            "-selectbackground",
                            stale["selection"],
                        )
                    for menu in tuple(app._themed_menus):
                        menu.configure(
                            background=stale["surface_alt"],
                            foreground=stale["text"],
                        )

                    app._set_theme(theme_key)
                    root.update_idletasks()
                    root.update()
                    palette = gui.THEMES[theme_key]
                    for combobox in base_comboboxes:
                        assert_popdown_palette(combobox, palette)
                    self.assertEqual(
                        palette["surface_alt"],
                        str(app.export_menu.cget("background")),
                    )
                    self.assertEqual(
                        palette["surface_alt"],
                        str(app.batch_menu.cget("background")),
                    )

                dialog = app.open_platform_config()
                app._set_theme("night")
                root.update_idletasks()
                root.update()
                for combobox in (
                    dialog.qianyi_model_box,
                    dialog.qianyi_route_box,
                    dialog.qianyi_local_runtime_box,
                    dialog.qianyi_lmstudio_model_box,
                    dialog.qianyi_lmstudio_profile_box,
                ):
                    assert_popdown_palette(combobox, gui.THEMES["night"])
                self.assertEqual(
                    gui.THEMES["night"]["input_bg"],
                    str(dialog.qianyi_provider_menu.cget("background")),
                )
                for switch in (
                    dialog.qianyi_mtp_switch,
                    dialog.qianyi_thinking_switch,
                ):
                    self.assertEqual(
                        gui.THEMES["night"]["bg"],
                        switch.canvas.cget("background"),
                    )

                app._set_theme("day")
                root.update_idletasks()
                root.update()
                for combobox in (
                    dialog.qianyi_model_box,
                    dialog.qianyi_route_box,
                    dialog.qianyi_local_runtime_box,
                    dialog.qianyi_lmstudio_model_box,
                    dialog.qianyi_lmstudio_profile_box,
                ):
                    assert_popdown_palette(combobox, gui.THEMES["day"])
                self.assertEqual(
                    gui.THEMES["day"]["input_bg"],
                    str(dialog.qianyi_provider_menu.cget("background")),
                )
                for switch in (
                    dialog.qianyi_mtp_switch,
                    dialog.qianyi_thinking_switch,
                ):
                    self.assertEqual(
                        gui.THEMES["day"]["bg"],
                        switch.canvas.cget("background"),
                    )
            finally:
                if dialog is not None and dialog.winfo_exists():
                    try:
                        dialog.grab_release()
                    except tk.TclError:
                        pass
                    dialog.destroy()
                root.update_idletasks()
                app.close()

    def test_rendered_native_text_colors_survive_theme_and_view_roundtrips(self):
        def rgb(value: str) -> tuple[int, int, int]:
            normalized = value.removeprefix("#")
            return tuple(
                int(normalized[index:index + 2], 16) for index in (0, 2, 4)
            )

        def dominant_screen_color(root: tk.Tk, widget: tk.Widget) -> tuple[int, int, int]:
            screen = ImageGrab.grab(window=root.winfo_id()).convert("RGB")
            scale_x = screen.width / max(1, root.winfo_width())
            scale_y = screen.height / max(1, root.winfo_height())
            inset = 14
            left = round(
                (widget.winfo_rootx() - root.winfo_rootx() + inset) * scale_x
            )
            top = round(
                (widget.winfo_rooty() - root.winfo_rooty() + inset) * scale_y
            )
            right = round(
                (
                    widget.winfo_rootx()
                    - root.winfo_rootx()
                    + widget.winfo_width()
                    - inset
                )
                * scale_x
            )
            bottom = round(
                (
                    widget.winfo_rooty()
                    - root.winfo_rooty()
                    + widget.winfo_height()
                    - inset
                )
                * scale_y
            )
            sample = screen.crop((left, top, right, bottom))
            pixels = (
                sample.get_flattened_data()
                if hasattr(sample, "get_flattened_data")
                else sample.getdata()
            )
            return Counter(pixels).most_common(1)[0][0]

        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.geometry("1400x900+20+20")
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            try:
                app.show_workspace()
                app.inspector_tabs.select(app.result_tab)
                app._set_theme("day")
                app.result_text.configure(
                    background=gui.THEMES["night"]["input_bg"]
                )
                app.result_text.frame.configure(
                    background=gui.THEMES["night"]["input_bg"]
                )
                app.show_project_center()
                app.show_workspace()
                app.inspector_tabs.select(app.result_tab)
                deadline = time.monotonic() + 0.35
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertEqual(
                    rgb(gui.THEMES["day"]["input_bg"]),
                    dominant_screen_color(root, app.result_text),
                )
                self.assertEqual(
                    gui.THEMES["day"]["input_bg"],
                    app.result_text.frame.cget("background"),
                )

                app.show_system_info()
                app._set_theme("night")
                app.system_release_notes.configure(
                    background=gui.THEMES["day"]["input_readonly"]
                )
                app.system_release_notes.frame.configure(
                    background=gui.THEMES["day"]["input_readonly"]
                )
                app._set_release_notes("主题渲染回归测试")
                deadline = time.monotonic() + 0.35
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)
                self.assertEqual(
                    rgb(gui.THEMES["night"]["input_readonly"]),
                    dominant_screen_color(root, app.system_release_notes),
                )
                self.assertEqual(
                    gui.THEMES["night"]["input_readonly"],
                    app.system_release_notes.frame.cget("background"),
                )
            finally:
                root.update_idletasks()
                app.close()

    def test_task_settings_scroll_keeps_seed_visible_and_uses_soft_borders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.geometry("1080x720+20+20")
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            try:
                app.show_workspace()
                deadline = time.monotonic() + 0.25
                while time.monotonic() < deadline:
                    root.update()
                    time.sleep(0.01)

                self.assertEqual("pack", app.sampling_shell.winfo_manager())
                shell_top = app.sampling_shell.winfo_rooty()
                shell_bottom = shell_top + app.sampling_shell.winfo_height()
                seed_top = app.seed_entry.winfo_rooty()
                seed_bottom = seed_top + app.seed_entry.winfo_height()
                self.assertGreaterEqual(seed_top, shell_top)
                self.assertLessEqual(seed_bottom, shell_bottom)
                self.assertEqual(
                    "SectionCard.TFrame", app.format_card.cget("style")
                )
                self.assertEqual(
                    "SectionCard.TFrame", app.strategy_card.cget("style")
                )

                style = ttk.Style(root)
                for theme_key in ("day", "night"):
                    app._set_theme(theme_key)
                    root.update_idletasks()
                    self.assertEqual(
                        0, int(style.lookup("TNotebook", "borderwidth"))
                    )
                    self.assertEqual(
                        gui.THEMES[theme_key]["border"],
                        style.lookup("TLabelframe", "bordercolor"),
                    )
                    self.assertEqual(
                        gui.THEMES[theme_key]["border"],
                        style.lookup("TNotebook.Tab", "lightcolor"),
                    )
                    self.assertEqual(
                        gui.THEMES[theme_key]["accent"],
                        style.lookup(
                            "Surface.TRadiobutton",
                            "indicatorcolor",
                            ("selected",),
                        ),
                    )
            finally:
                root.update_idletasks()
                app.close()

    def test_form_controls_ignore_mousewheel_without_blocking_inspector_scroll(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.geometry("1080x720+20+20")
            app = gui.CaptionApp(
                root, self._store(Path(directory)), show_splash=False
            )
            try:
                app.show_workspace()
                app.right_tabs.select(app.task_settings_tab)
                app._toggle_sampling_panel(True)
                root.update_idletasks()
                root.update()

                app.focus_box.current(1)
                focus_before = app.focus_box.get()
                app.concurrency_var.set(3)
                concurrency_before = app.concurrency_var.get()
                app.sampling_preset_box.current(1)
                preset_before = app.sampling_preset_box.get()
                temperature = app.sampling_control_widgets["Temperature"]
                app.temperature_var.set(0.7)
                temperature_before = app.temperature_var.get()

                with mock.patch.object(
                    app,
                    "_task_settings_mousewheel",
                    wraps=app._task_settings_mousewheel,
                ) as inspector_scroll:
                    for widget in (
                        app.focus_box,
                        app.concurrency_box,
                        app.sampling_preset_box,
                        temperature,
                    ):
                        widget.event_generate("<MouseWheel>", delta=-120)
                        root.update()

                self.assertEqual(focus_before, app.focus_box.get())
                self.assertEqual(concurrency_before, app.concurrency_var.get())
                self.assertEqual(preset_before, app.sampling_preset_box.get())
                self.assertEqual(temperature_before, app.temperature_var.get())
                self.assertEqual(4, inspector_scroll.call_count)
            finally:
                root.update_idletasks()
                app.close()


if __name__ == "__main__":
    unittest.main()
