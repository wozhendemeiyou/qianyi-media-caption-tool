import csv
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

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
    def test_public_defaults_contain_no_private_api_or_prompt_data(self):
        self.assertEqual({}, core.DEFAULT_SETTINGS["api_models"])
        self.assertEqual({}, core.DEFAULT_SETTINGS["api_endpoints"])
        self.assertEqual("", core.DEFAULT_SETTINGS["custom_api_endpoint"])
        self.assertEqual("", core.DEFAULT_SETTINGS["user_prompt"])
        self.assertEqual("", core.DEFAULT_SETTINGS["selected_preset"])
        self.assertEqual({}, core.DEFAULT_SETTINGS["prompt_presets"])
        self.assertFalse(core.DEFAULT_SETTINGS["enable_mtp"])
        self.assertTrue(core.DEFAULT_SETTINGS["remove_thinking_tags"])
        self.assertEqual("huggingface", core.DEFAULT_SETTINGS["local_runtime"])
        self.assertEqual(
            "low_vram", core.DEFAULT_SETTINGS["lmstudio_load_profile"]
        )
        self.assertEqual(
            "http://localhost:1234/v1",
            core.DEFAULT_SETTINGS["lmstudio_base_url"],
        )

    def test_thinking_sections_are_removed_without_touching_final_caption(self):
        self.assertEqual(
            "最终标注",
            core.strip_thinking_sections(
                "<think>先分析主体和背景</think>\n最终标注"
            ),
        )
        self.assertEqual(
            "final caption",
            core.strip_thinking_sections(
                "hidden reasoning</analysis>\nfinal caption"
            ),
        )
        self.assertEqual(
            "保留正文",
            core.strip_thinking_sections(
                "[reasoning]内部推理[/reasoning]\n保留正文"
            ),
        )
        self.assertEqual(
            "final caption",
            core._chat_text({
                "choices": [{
                    "message": {
                        "content": [
                            {"type": "reasoning", "text": "hidden"},
                            {"type": "text", "text": "final caption"},
                        ]
                    }
                }]
            }),
        )

    def test_qwen_can_disable_thinking_and_clean_returned_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)
            calls = []

            def sender(method, url, **kwargs):
                calls.append(kwargs["json"])
                return FakeResponse(payload={
                    "choices": [{
                        "message": {
                            "content": "<think>分析画面</think>\n干净标注"
                        }
                    }]
                })

            client = core.CaptionClient(
                core.MODELS[core.DEFAULT_MODEL_KEY],
                "qwen-key",
                core.CancellationToken(),
                core.HttpTransport(sender),
                provider_key="qwen",
                api_model="qwen3.8-max",
                remove_thinking_tags=True,
            )

            self.assertEqual("干净标注", client.caption_image(image_path, "prompt"))
            self.assertIs(False, calls[0]["enable_thinking"])

    def test_local_mtp_requires_model_layers_and_generation_support(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text("{}", encoding="utf-8")
            client = core.LocalCaptionClient(
                root,
                core.CancellationToken(),
                enable_mtp=True,
            )

            class ModelConfig:
                num_nextn_predict_layers = 1

            class GenerationConfig:
                use_mtp = False

            class Model:
                config = ModelConfig()
                generation_config = GenerationConfig()

            client.model = Model()
            self.assertTrue(client._supports_native_mtp())
            client.model.generation_config = object()
            self.assertFalse(client._supports_native_mtp())

    def test_windows_version_resource_matches_app_version(self):
        project_root = Path(core.__file__).resolve().parent
        version_resource = (
            project_root / "assets" / "qianyi-version-info.txt"
        ).read_text(encoding="utf-8")
        numeric_parts = [int(part) for part in re.findall(r"\d+", core.APP_VERSION)]
        version_parts = tuple((numeric_parts + [0, 0, 0])[:3])
        version_quad = (*version_parts, 0)
        formatted_quad = ", ".join(str(part) for part in version_quad)
        self.assertIn(f"filevers=({formatted_quad})", version_resource)
        self.assertIn(f"prodvers=({formatted_quad})", version_resource)
        for key, value in {
            "CompanyName": "芊熠智能",
            "FileDescription": "芊熠智能打标工作台",
            "FileVersion": core.APP_VERSION,
            "InternalName": "QianyiMediaCaptionTool",
            "OriginalFilename": "Qianyi-MediaCaptionTool.exe",
            "ProductName": "芊熠智能打标工作台",
            "ProductVersion": core.APP_VERSION,
        }.items():
            self.assertRegex(
                version_resource,
                rf'StringStruct\(\s*"{re.escape(key)}",\s*'
                rf'"{re.escape(value)}"\s*\)',
            )
        spec_path = project_root / f"MediaCaptionTool-{core.APP_VERSION}.spec"
        self.assertTrue(spec_path.is_file())
        spec_text = spec_path.read_text(encoding="utf-8")
        self.assertIn(
            'version="assets/qianyi-version-info.txt"', spec_text
        )
        self.assertIn(
            f'name="MediaCaptionTool-{core.APP_VERSION}-Studio"',
            spec_text,
        )

    def test_github_release_version_comparison_and_payload(self):
        self.assertEqual((3, 10, 2), core.version_tuple("release-v3.10.2"))
        self.assertTrue(core.is_newer_version("v3.7", "3.6"))
        self.assertFalse(core.is_newer_version("v3.5.0", "3.5"))
        self.assertFalse(core.is_newer_version("invalid", "3.5"))

        transport = core.HttpTransport(
            lambda method, url, **kwargs: FakeResponse(
                payload={
                    "tag_name": "v3.7",
                    "name": "芊熠智能打标工作台 v3.7",
                    "html_url": "https://github.com/example/releases/tag/v3.7",
                    "published_at": "2026-08-13T10:00:00Z",
                    "body": "- 新增更新检查",
                    "assets": [{
                        "name": "Qianyi-v3.7-Windows-x64.zip",
                        "browser_download_url": (
                            "https://github.com/example/releases/download/v3.7/"
                            "Qianyi-v3.7-Windows-x64.zip"
                        ),
                        "size": 1234,
                        "digest": "sha256:" + "a" * 64,
                        "content_type": "application/zip",
                    }],
                }
            )
        )
        release = core.check_latest_release(transport)
        self.assertTrue(release["is_newer"])
        self.assertEqual("v3.7", release["tag"])
        self.assertIn("更新检查", release["notes"])
        self.assertEqual("Qianyi-v3.7-Windows-x64.zip", release["windows_asset"]["name"])
        self.assertEqual("a" * 64, release["windows_asset"]["sha256"])

    def test_openai_compatible_provider_routes_model_and_supported_sampling(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)
            calls = []

            def sender(method, url, **kwargs):
                calls.append((method, url, kwargs))
                return FakeResponse(
                    payload={"choices": [{"message": {"content": "caption"}}]}
                )

            client = core.CaptionClient(
                core.MODELS[core.DEFAULT_MODEL_KEY],
                "router-key",
                core.CancellationToken(),
                core.HttpTransport(sender),
                provider_key="openai",
                api_model="gpt-4.1",
                sampling={
                    "max_tokens": 900,
                    "temperature": 0.35,
                    "top_p": 0.8,
                    "top_k": 40,
                    "frequency_penalty": 0.2,
                    "presence_penalty": 0.1,
                    "seed": 7,
                },
            )

            self.assertEqual("caption", client.caption_image(image_path, "prompt"))
            payload = calls[0][2]["json"]
            self.assertEqual(core.API_PROVIDERS["openai"].chat_url, calls[0][1])
            self.assertEqual("gpt-4.1", payload["model"])
            self.assertEqual(900, payload["max_tokens"])
            self.assertEqual(7, payload["seed"])
            self.assertNotIn("top_k", payload)

    def test_openai_gpt5_uses_completion_tokens_and_no_reasoning(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)
            calls = []

            def sender(method, url, **kwargs):
                calls.append((method, url, kwargs))
                return FakeResponse(
                    payload={"choices": [{"message": {"content": "caption"}}]}
                )

            client = core.CaptionClient(
                core.MODELS[core.DEFAULT_MODEL_KEY],
                "openai-key",
                core.CancellationToken(),
                core.HttpTransport(sender),
                provider_key="openai",
                api_model="gpt-5.6-terra",
                sampling={"max_tokens": 1800},
            )

            self.assertEqual("caption", client.caption_image(image_path, "prompt"))
            payload = calls[0][2]["json"]
            self.assertEqual(1800, payload["max_completion_tokens"])
            self.assertEqual("none", payload["reasoning_effort"])
            self.assertNotIn("max_tokens", payload)

    def test_current_provider_registry_contains_requested_visual_models(self):
        expected = {
            "google": (
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                "gemini-3.7-flash",
            ),
            "moonshot": (
                "https://api.moonshot.cn/v1/chat/completions",
                "kimi-k3",
            ),
            "qwen": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                "qwen3.8-max",
            ),
            "siliconflow": (
                "https://api.siliconflow.cn/v1/chat/completions",
                "Qwen/Qwen3.6-35B-A3B",
            ),
        }
        for provider_key, (url, model) in expected.items():
            provider = core.API_PROVIDERS[provider_key]
            self.assertEqual(url, provider.chat_url)
            self.assertEqual(model, provider.default_model)
            self.assertIn(model, provider.model_suggestions)
        self.assertIn("gpt-5.6-sol", core.API_PROVIDERS["openai"].model_suggestions)
        self.assertIn("gpt-5.5", core.API_PROVIDERS["openai"].model_suggestions)
        self.assertIn("gpt-5.4-nano", core.API_PROVIDERS["openai"].model_suggestions)
        self.assertIn("custom", core.API_PROVIDERS)
        self.assertEqual("", core.API_PROVIDERS["custom"].default_model)
        self.assertIn("minimax-m3", core.MODELS)
        self.assertEqual(core.CODING_CHAT_URL, core.MODELS["minimax-m3"].chat_url)
        self.assertNotIn("minimax", core.API_PROVIDERS)
        self.assertNotIn("openrouter", core.API_PROVIDERS)

    def test_provider_connection_uses_non_generating_models_endpoint(self):
        calls = []

        def sender(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse(payload={"data": []})

        result = core.test_provider_connection(
            "openai", "test-key", core.HttpTransport(sender)
        )
        self.assertTrue(result["ok"])
        self.assertEqual("GET", calls[0][0])
        self.assertEqual(core.PROVIDER_TEST_URLS["openai"], calls[0][1])
        self.assertEqual("Bearer test-key", calls[0][2]["headers"]["Authorization"])

        calls.clear()
        result = core.test_provider_connection(
            "custom",
            "",
            core.HttpTransport(sender),
            api_endpoint="https://example.test/v1",
        )
        self.assertTrue(result["ok"])
        self.assertEqual("https://example.test/v1/models", calls[0][1])
        self.assertNotIn("Authorization", calls[0][2]["headers"])

    def test_lmstudio_model_discovery_uses_local_models_endpoint(self):
        calls = []

        def sender(method, url, **kwargs):
            calls.append((method, url, kwargs))
            return FakeResponse(payload={
                "models": [
                    {
                        "type": "llm",
                        "key": "qwen-vl-local",
                        "display_name": "Qwen VL",
                        "capabilities": {
                            "vision": True,
                            "reasoning": {
                                "allowed_options": ["off", "on"],
                                "default": "on",
                            },
                        },
                        "size_bytes": 123456,
                        "params_string": "7B",
                        "loaded_instances": [{
                            "id": "qwen-vl-local",
                            "config": {"context_length": 8192, "parallel": 1},
                        }],
                    },
                    {
                        "type": "llm",
                        "key": "text-only-local",
                        "capabilities": {"vision": False},
                        "loaded_instances": [],
                    },
                    {
                        "type": "llm",
                        "key": "gemma-vision-local",
                        "capabilities": {"vision": True},
                        "loaded_instances": [],
                    },
                ]
            })

        models = core.discover_lmstudio_models(
            "http://localhost:1234/v1",
            core.HttpTransport(sender),
        )

        self.assertEqual(
            ["qwen-vl-local", "gemma-vision-local"], models
        )
        self.assertEqual("GET", calls[0][0])
        self.assertEqual("http://localhost:1234/api/v1/models", calls[0][1])
        self.assertNotIn("Authorization", calls[0][2]["headers"])

        inventory = core.list_lmstudio_models(
            "http://localhost:1234/v1",
            core.HttpTransport(sender),
        )
        self.assertEqual(["qwen-vl-local"], inventory[0]["loaded_instances"])
        self.assertEqual(
            {"context_length": 8192, "parallel": 1},
            inventory[0]["loaded_configs"]["qwen-vl-local"],
        )
        self.assertEqual("on", inventory[0]["reasoning"]["default"])
        self.assertEqual(123456, inventory[0]["size_bytes"])

    def test_lmstudio_load_and_unload_use_native_management_api(self):
        calls = []

        def sender(method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/load"):
                return FakeResponse(payload={
                    "type": "llm",
                    "instance_id": "vision-instance-1",
                    "status": "loaded",
                })
            return FakeResponse(payload={
                "instance_id": "vision-instance-1",
                "status": "unloaded",
            })

        transport = core.HttpTransport(sender)
        loaded = core.load_lmstudio_model(
            "http://localhost:1234/v1", "qwen-vl-local", transport
        )
        unloaded = core.unload_lmstudio_model(
            "http://localhost:1234/v1", loaded["instance_id"], transport
        )

        self.assertEqual("vision-instance-1", loaded["instance_id"])
        self.assertEqual("unloaded", unloaded["status"])
        self.assertEqual(
            "http://localhost:1234/api/v1/models/load", calls[0][1]
        )
        self.assertEqual(
            {
                "model": "qwen-vl-local",
                "echo_load_config": True,
                "context_length": core.LMSTUDIO_LOAD_CONTEXT_LENGTH,
                "eval_batch_size": 512,
                "flash_attention": True,
                "offload_kv_cache_to_gpu": False,
            },
            calls[0][2]["json"],
        )
        self.assertEqual(
            "http://localhost:1234/api/v1/models/unload", calls[1][1]
        )
        self.assertEqual(
            {"instance_id": "vision-instance-1"}, calls[1][2]["json"]
        )

    def test_lmstudio_caption_uses_openai_image_request_and_cleans_thinking(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)
            calls = []

            def sender(method, url, **kwargs):
                calls.append((method, url, kwargs))
                return FakeResponse(payload={
                    "choices": [{
                        "message": {
                            "content": "<think>分析图片</think>\n本地标注"
                        }
                    }]
                })

            client = core.LmStudioCaptionClient(
                "http://localhost:1234/v1",
                "qwen-vl-local",
                core.CancellationToken(),
                core.HttpTransport(sender),
                sampling={"max_tokens": 512, "top_k": 40, "seed": 7},
                remove_thinking_tags=True,
            )

            self.assertEqual(
                "本地标注", client.caption_image(image_path, "描述图片")
            )
            method, url, kwargs = calls[0]
            self.assertEqual("POST", method)
            self.assertEqual(
                "http://localhost:1234/v1/chat/completions", url
            )
            self.assertNotIn("Authorization", kwargs["headers"])
            payload = kwargs["json"]
            self.assertEqual("qwen-vl-local", payload["model"])
            self.assertEqual(
                core.LMSTUDIO_CAPTION_TOKEN_LIMIT,
                payload["max_tokens"],
            )
            self.assertEqual("none", payload["reasoning_effort"])
            for parameter in (
                "temperature", "top_p", "top_k", "seed"
            ):
                self.assertNotIn(parameter, payload)
            self.assertEqual(
                (10, core.LMSTUDIO_READ_TIMEOUT), kwargs["timeout"]
            )
            image_url = payload["messages"][0]["content"][0]["image_url"]["url"]
            self.assertTrue(image_url.startswith("data:image/jpeg;base64,"))

    def test_hf_local_caption_explicitly_toggles_thinking_in_template(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)

            class StopAfterTemplate(RuntimeError):
                pass

            template_calls = []

            class FakeProcessor:
                def apply_chat_template(self, messages, **kwargs):
                    template_calls.append(kwargs)
                    raise StopAfterTemplate

            client = core.LocalCaptionClient.__new__(core.LocalCaptionClient)
            client.model_folder = Path(directory)
            client.token = core.CancellationToken()
            client.processor = FakeProcessor()
            client.model = object()
            client.torch = None
            client.device = "cpu"
            client.sampling = core.normalize_sampling({
                "max_tokens": 512,
                "temperature": 0.4,
                "top_p": 0.8,
                "top_k": 32,
                "seed": 11,
            })
            client.enable_mtp = False
            client.mtp_active = False
            client.remove_thinking_tags = False

            with self.assertRaises(StopAfterTemplate):
                client.caption_image(image_path, "描述图片")

            self.assertEqual(1, len(template_calls))
            self.assertIs(template_calls[0]["enable_thinking"], True)

            client.remove_thinking_tags = True
            template_calls.clear()
            with self.assertRaises(StopAfterTemplate):
                client.caption_image(image_path, "描述图片")
            self.assertIs(template_calls[0]["enable_thinking"], False)

    def test_lmstudio_low_vram_loader_uses_cli_resource_guards(self):
        completed = mock.Mock(returncode=0, stdout="loaded", stderr="")
        inventory = [{
            "key": "qwen-vl-local",
            "loaded_instances": ["qwen-vl-local"],
            "loaded_configs": {
                "qwen-vl-local": {"context_length": 8192, "parallel": 1}
            },
        }]
        with mock.patch.object(
            core, "find_lmstudio_cli", return_value=Path("C:/fake/lms.exe")
        ):
            with mock.patch.object(core.subprocess, "run", return_value=completed) as run:
                with mock.patch.object(
                    core, "list_lmstudio_models", return_value=inventory
                ):
                    result = core.load_lmstudio_model(
                        "http://localhost:1234/v1",
                        "qwen-vl-local",
                        load_profile="low_vram",
                    )

        command = run.call_args.args[0]
        self.assertIn("--gpu", command)
        self.assertEqual("0.10", command[command.index("--gpu") + 1])
        self.assertEqual("8192", command[command.index("--context-length") + 1])
        self.assertEqual("1", command[command.index("--parallel") + 1])
        self.assertIn("--no-speculative-draft-mtp", command)
        self.assertEqual("cli", result["loader"])
        self.assertEqual(8192, result["load_config"]["context_length"])

    def test_lmstudio_terminated_error_is_translated(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)

            def sender(method, url, **kwargs):
                return FakeResponse(
                    status_code=400,
                    payload={"error": "terminated"},
                )

            client = core.LmStudioCaptionClient(
                "http://localhost:1234/v1",
                "qwen-vl-local",
                core.CancellationToken(),
                core.HttpTransport(sender),
            )
            with self.assertRaisesRegex(RuntimeError, "模型进程.*退出"):
                client.caption_image(image_path, "描述图片")

    def test_lmstudio_rejects_reasoning_only_length_result(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)

            def sender(method, url, **kwargs):
                return FakeResponse(payload={
                    "choices": [{
                        "finish_reason": "length",
                        "message": {
                            "content": "",
                            "reasoning_content": "仍在分析",
                        },
                    }],
                    "usage": {
                        "completion_tokens_details": {"reasoning_tokens": 768}
                    },
                })

            client = core.LmStudioCaptionClient(
                "http://localhost:1234/v1",
                "qwen-vl-local",
                core.CancellationToken(),
                core.HttpTransport(sender),
            )
            with self.assertRaisesRegex(RuntimeError, "思考内容占用"):
                client.caption_image(image_path, "描述图片")

    def test_lmstudio_rejects_degenerate_repeated_character_output(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.jpg"
            Image.new("RGB", (24, 24), "white").save(image_path)

            def sender(method, url, **kwargs):
                return FakeResponse(payload={
                    "choices": [{
                        "message": {"content": "?" * 64}
                    }]
                })

            client = core.LmStudioCaptionClient(
                "http://localhost:1234/v1",
                "broken-local-model",
                core.CancellationToken(),
                core.HttpTransport(sender),
            )

            with self.assertRaisesRegex(RuntimeError, "连续重复"):
                client.caption_image(image_path, "describe")

    def test_update_package_extracts_valid_executable_and_writes_updater(self):
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "Qianyi-Windows-x64.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("Qianyi/README.txt", "notes")
                archive.writestr("Qianyi/MediaCaptionTool.exe", b"MZ" + b"x" * 128)
            extracted = core.extract_update_executable(package, root / "payload")
            self.assertEqual(b"MZ", extracted.read_bytes()[:2])
            installed = root / "installed" / "MediaCaptionTool.exe"
            installed.parent.mkdir()
            installed.write_bytes(b"MZold")
            script = core.create_windows_update_script(
                extracted,
                installed,
                12345,
                root / "updater",
                bootloader_pid=54321,
            )
            script_text = script.read_text(encoding="utf-8-sig")
            self.assertIn("Wait-Process -Id 12345", script_text)
            self.assertIn("Wait-Process -Id 54321", script_text)
            self.assertIn("Start-Sleep -Milliseconds 750", script_text)
            self.assertIn("Copy-Item -LiteralPath", script_text)
            self.assertIn("$stagedHash = Get-Sha256 $staged", script_text)
            self.assertIn(
                "$env:PYINSTALLER_RESET_ENVIRONMENT = '1'", script_text
            )
            self.assertIn("$_.Name -like '_PYI_*'", script_text)
            self.assertIn("Start-Process -FilePath $target -WorkingDirectory", script_text)

    def test_update_launcher_resets_pyinstaller_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "install-qianyi-update.ps1"
            script.write_text("exit 0", encoding="utf-8")
            process = mock.Mock()
            with mock.patch.object(core.subprocess, "Popen", return_value=process) as popen:
                self.assertIs(process, core.launch_windows_update_installer(script))
            environment = popen.call_args.kwargs["env"]
            self.assertEqual("1", environment["PYINSTALLER_RESET_ENVIRONMENT"])

    @unittest.skipUnless(core.os.name == "nt", "Windows updater integration")
    def test_windows_update_launcher_replaces_target_without_detached_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system32 = Path(core.os.environ["WINDIR"]) / "System32"
            source = root / "payload" / "source.exe"
            installed = root / "installed" / "target.exe"
            source.parent.mkdir()
            installed.parent.mkdir()
            source.write_bytes((system32 / "whoami.exe").read_bytes())
            installed.write_bytes((system32 / "where.exe").read_bytes())
            expected = core.hashlib.sha256(source.read_bytes()).hexdigest()
            updater_dir = root / "updater"
            helper = (
                "from pathlib import Path; import os, sys; "
                "from media_caption_core import "
                "create_windows_update_script, launch_windows_update_installer; "
                "script=create_windows_update_script("
                "Path(sys.argv[1]), Path(sys.argv[2]), os.getpid(), "
                "Path(sys.argv[3]), restart=False); "
                "launch_windows_update_installer(script)"
            )
            completed = core.subprocess.run(
                [
                    core.sys.executable,
                    "-c",
                    helper,
                    str(source),
                    str(installed),
                    str(updater_dir),
                ],
                cwd=Path(__file__).resolve().parents[1],
                timeout=8,
                check=False,
            )
            self.assertEqual(0, completed.returncode)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if core.hashlib.sha256(installed.read_bytes()).hexdigest() == expected:
                    break
                time.sleep(0.05)

            self.assertEqual(
                expected, core.hashlib.sha256(installed.read_bytes()).hexdigest()
            )
            script = updater_dir / "install-qianyi-update.ps1"
            self.assertFalse(script.exists())
            self.assertIn(
                "SUCCESS",
                (updater_dir / "update-install.log").read_text(
                    encoding="utf-8-sig"
                ),
            )

    def test_custom_endpoint_and_sampling_values_are_normalized(self):
        self.assertEqual(
            "http://127.0.0.1:8000/v1/chat/completions",
            core.custom_chat_url("http://127.0.0.1:8000/v1"),
        )
        sampling = core.normalize_sampling({
            "max_tokens": 2,
            "temperature": 99,
            "top_p": -1,
            "top_k": 999,
            "seed": "invalid",
        })
        self.assertEqual(64, sampling["max_tokens"])
        self.assertEqual(2.0, sampling["temperature"])
        self.assertEqual(0.0, sampling["top_p"])
        self.assertEqual(500, sampling["top_k"])
        self.assertIsNone(sampling["seed"])

    def test_provider_secrets_are_isolated_when_stores_are_supplied(self):
        default_secret = MemorySecretStore()
        openai_secret = MemorySecretStore()
        store = core.SettingsStore(
            Path("settings.json"),
            Path("legacy.json"),
            default_secret,
            provider_secret_stores={"openai": openai_secret},
        )

        store.set_api_key("volc-key")
        store.set_api_key("openai-key", "openai")

        self.assertEqual("volc-key", store.get_api_key())
        self.assertEqual("openai-key", store.get_api_key("openai"))
        store.set_api_key("", "openai")
        self.assertEqual("volc-key", store.get_api_key())
        self.assertEqual("", store.get_api_key("openai"))

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
        self.assertEqual("豆包 Seed 2.1 Turbo", turbo.label)
        self.assertEqual(core.CODING_CHAT_URL, turbo.chat_url_for(core.date(2026, 8, 8)))
        self.assertEqual("Coding Plan", turbo.billing_label(core.date(2026, 8, 8)))
        self.assertEqual(core.CODING_CHAT_URL, legacy.chat_url_for(core.date(2026, 8, 7)))
        self.assertEqual(
            "Coding Plan（8月8日下线）",
            legacy.billing_label(core.date(2026, 8, 7)),
        )
        self.assertEqual(core.STANDARD_CHAT_URL, legacy.chat_url_for(core.date(2026, 8, 8)))
        self.assertEqual("按量计费", legacy.billing_label(core.date(2026, 8, 8)))

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

    def test_lmstudio_backend_preserves_user_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(3):
                Image.new("RGB", (24, 24), "white").save(
                    root / f"image-{index}.jpg"
                )

            captured = {}

            class FakeLmStudioClient:
                def __init__(
                    self, base_url, model_id, token, transport,
                    *, sampling, remove_thinking_tags,
                ):
                    captured.update({
                        "base_url": base_url,
                        "model_id": model_id,
                        "sampling": sampling,
                        "remove_thinking_tags": remove_thinking_tags,
                    })

                def caption_image(self, path, prompt):
                    return f"caption {path.stem}"

            data_root = root / "appdata"
            with mock.patch.object(
                core, "LmStudioCaptionClient", FakeLmStudioClient
            ):
                with mock.patch.object(core, "app_data_dir", return_value=data_root):
                    summary = core.BatchRunner(
                        lambda kind, payload: None
                    ).run(
                        root,
                        "image",
                        "prompt",
                        "seed-2.1-pro",
                        "",
                        backend="local",
                        local_runtime="lmstudio",
                        lmstudio_base_url="http://localhost:1234/v1",
                        lmstudio_model="qwen-vl-local",
                        concurrency=4,
                    )

            self.assertEqual(3, summary.success)
            self.assertEqual("qwen-vl-local", captured["model_id"])
            state_path = core.project_data_path(root, data_root) / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(4, state["metadata"]["concurrency"])
            self.assertEqual("lmstudio", state["metadata"]["provider"])

    def test_local_backend_rejects_non_model_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "config.json"):
                core.LocalCaptionClient(
                    Path(directory),
                    core.CancellationToken(),
                )

    def test_llama_cpp_server_command_contains_gguf_and_mmproj(self):
        client = core.LlamaCppCaptionClient.__new__(
            core.LlamaCppCaptionClient
        )
        client.server_path = Path(r"C:\Tools\llama-server.exe")
        client.model_path = Path(r"E:\Models\vision.gguf")
        client.mmproj_path = Path(r"E:\Models\mmproj-F16.gguf")
        client.model_alias = "vision-local"
        client.context_length = 4096
        client.gpu_layers = 24
        command = client._server_command(18088)
        self.assertEqual(
            [
                r"C:\Tools\llama-server.exe",
                "-m", r"E:\Models\vision.gguf",
                "--mmproj", r"E:\Models\mmproj-F16.gguf",
                "--host", "127.0.0.1",
                "--port", "18088",
                "-c", "4096",
                "--alias", "vision-local",
                "-ngl", "24",
            ],
            command,
        )

    def test_llama_cpp_sampling_payload_includes_full_local_controls(self):
        client = core.LlamaCppCaptionClient.__new__(
            core.LlamaCppCaptionClient
        )
        client.sampling = core.normalize_sampling({
            "max_tokens": 1024,
            "temperature": 0.25,
            "top_p": 0.85,
            "top_k": 20,
            "frequency_penalty": 0.15,
            "presence_penalty": 0.2,
            "seed": 7,
        })
        client.remove_thinking_tags = True
        payload = client._sampling_payload()
        self.assertEqual(1024, payload["max_tokens"])
        self.assertEqual(0.25, payload["temperature"])
        self.assertEqual(0.85, payload["top_p"])
        self.assertEqual(20, payload["top_k"])
        self.assertEqual(0.15, payload["frequency_penalty"])
        self.assertEqual(0.2, payload["presence_penalty"])
        self.assertEqual(7, payload["seed"])
        # llama.cpp's current Qwen vision templates accept low/medium/xhigh;
        # the client uses low when the UI asks to remove thinking output.
        self.assertEqual("low", payload["reasoning_effort"])

    def test_settings_normalize_llama_cpp_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = core.SettingsStore(
                root / "settings.json",
                root / "legacy.json",
                MemorySecretStore(),
            )
            settings = store.load()
            settings.update({
                "local_runtime": "llamacpp",
                "llama_server_path": "  C:/Tools/llama-server.exe  ",
                "llama_model_path": "  E:/Models/vision.gguf  ",
                "llama_mmproj_path": "  E:/Models/mmproj-F16.gguf  ",
                "llama_context_length": "8192",
                "llama_gpu_layers": "-1",
            })
            store.save(settings)
            restored = store.load()
            self.assertEqual("llamacpp", restored["local_runtime"])
            self.assertEqual(
                "C:/Tools/llama-server.exe", restored["llama_server_path"]
            )
            self.assertEqual(8192, restored["llama_context_length"])
            self.assertEqual(-1, restored["llama_gpu_layers"])

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
            core.write_caption(path, "replacement caption")
            self.assertEqual(
                "replacement caption",
                core.caption_path_for(path).read_text(encoding="utf-8"),
            )
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
            migrated_legacy = json.loads(legacy.read_text(encoding="utf-8"))
            self.assertNotIn("api_key", migrated_legacy)

            store.set_api_key("")
            self.assertEqual("", secrets.get())
            restarted = core.SettingsStore(settings_path, legacy, secrets)
            restarted.load()
            self.assertEqual("", secrets.get())

    @unittest.skipUnless(os.name == "nt", "DPAPI is only available on Windows")
    def test_dpapi_secret_store_deletes_encrypted_file_when_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            secret_path = Path(directory) / "credentials.bin"
            secrets = core.DpapiSecretStore(secret_path)

            secrets.set("temporary-test-key")
            self.assertTrue(secret_path.is_file())
            self.assertEqual("temporary-test-key", secrets.get())

            secrets.set("")
            self.assertFalse(secret_path.exists())
            self.assertEqual("", secrets.get())

    def test_settings_recover_from_malformed_persisted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings_path = root / "settings.json"
            settings_path.write_text(
                json.dumps({
                    "concurrency": "not-a-number",
                    "recent_folders": "C:/not-a-list",
                    "prompt_presets": ["not", "a", "mapping"],
                    "user_prompt": ["not", "text"],
                    "suppress_seed_2_0_shutdown_notice": "true",
                    "theme": "neon",
                    "local_runtime": "unknown",
                    "lmstudio_base_url": "   ",
                    "lmstudio_model": ["not", "text"],
                    "lmstudio_load_profile": "unsafe",
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
            self.assertEqual("", settings["user_prompt"])
            self.assertEqual("night", settings["theme"])
            self.assertEqual("huggingface", settings["local_runtime"])
            self.assertEqual(
                "http://localhost:1234/v1", settings["lmstudio_base_url"]
            )
            self.assertEqual("", settings["lmstudio_model"])
            self.assertEqual("low_vram", settings["lmstudio_load_profile"])
            self.assertNotIn("suppress_seed_2_0_shutdown_notice", settings)

            settings["concurrency"] = "also-invalid"
            settings["theme"] = "invalid"
            store.save(settings)
            persisted = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(3, persisted["concurrency"])
            self.assertEqual("night", persisted["theme"])
            self.assertEqual(14, persisted["version"])
            self.assertNotIn("suppress_seed_2_0_shutdown_notice", persisted)

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
            events = []
            with mock.patch.object(core, "app_data_dir", return_value=data_root):
                runner = core.BatchRunner(
                    lambda kind, payload: events.append((kind, payload)),
                    core.HttpTransport(sender),
                )
                summary = runner.run(root, "image", "prompt", "seed-2.1-pro", "key")
            self.assertEqual(1, summary.success)
            self.assertEqual("new caption", core.caption_path_for(image_path).read_text(encoding="utf-8"))
            success_event = next(
                payload
                for kind, payload in events
                if kind == "status" and payload["status"] == "success"
            )
            self.assertGreaterEqual(success_event["elapsed_seconds"], 0)
            self.assertEqual(10, success_event["character_count"])
            self.assertGreater(success_event["characters_per_second"], 0)
            self.assertEqual(10, summary.characters)
            self.assertGreaterEqual(summary.elapsed_seconds, 0)
            states = list(data_root.glob("projects/*/state.json"))
            self.assertEqual(1, len(states))
            state = json.loads(states[0].read_text(encoding="utf-8"))
            self.assertEqual("completed", state["status"])
            self.assertEqual(10, state["summary"]["characters"])
            self.assertGreaterEqual(state["summary"]["elapsed_seconds"], 0)
            self.assertEqual("success", next(iter(state["items"].values()))["status"])
            self.assertEqual(1, len(list(data_root.glob("projects/*/run-*.jsonl"))))
            self.assertEqual(1, len(list(data_root.glob("projects/*/last-failures.csv"))))

    def test_single_run_can_return_caption_without_writing_txt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "image.jpg"
            Image.new("RGB", (24, 24)).save(image_path)

            def sender(method, url, **kwargs):
                return FakeResponse(
                    payload={"choices": [{"message": {"content": "standalone caption"}}]}
                )

            events = []
            with mock.patch.object(core, "app_data_dir", return_value=root / "appdata"):
                runner = core.BatchRunner(
                    lambda kind, payload: events.append((kind, payload)),
                    core.HttpTransport(sender),
                )
                summary = runner.run(
                    root,
                    "image",
                    "prompt",
                    "seed-2.1-pro",
                    "key",
                    write_output=False,
                )

            self.assertEqual(1, summary.success)
            self.assertFalse(core.caption_path_for(image_path).exists())
            success = next(
                payload
                for kind, payload in events
                if kind == "status" and payload["status"] == "success"
            )
            self.assertEqual("standalone caption", success["detail"])

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

    def test_stale_running_project_is_reported_as_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            state_path = core.project_data_path(media, root / "appdata") / "state.json"
            core.atomic_write_json(
                state_path,
                {
                    "status": "running",
                    "owner_pid": 99999999,
                    "summary": {"total": 3, "success": 1},
                },
            )
            summary = core.load_project_summary(media, root / "appdata")
            self.assertEqual("interrupted", summary["status"])

    def test_incomplete_paths_restore_pending_and_cancelled_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "media"
            media.mkdir()
            pending = media / "pending.jpg"
            cancelled = media / "cancelled.jpg"
            complete = media / "complete.jpg"
            for path in (pending, cancelled, complete):
                Image.new("RGB", (8, 8)).save(path)
            core.write_caption(complete, "done")
            state_path = core.project_data_path(media, root / "appdata") / "state.json"
            core.atomic_write_json(
                state_path,
                {
                    "status": "stopped",
                    "metadata": {"mode": "image"},
                    "items": {
                        "pending.jpg": {"status": "pending"},
                        "cancelled.jpg": {"status": "cancelled"},
                        "complete.jpg": {"status": "success"},
                    },
                },
            )
            self.assertEqual(
                [cancelled, pending],
                core.load_incomplete_paths(media, "image", root / "appdata"),
            )

    def test_backup_and_diagnostics_exclude_credentials_and_caption_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "appdata"
            project = data_root / "projects" / "project-1"
            project.mkdir(parents=True)
            core.atomic_write_json(
                data_root / "settings.json",
                {"api_key": "plain-secret", "theme": "day"},
            )
            (data_root / "credentials.bin").write_bytes(b"encrypted-secret")
            core.atomic_write_json(
                project / "state.json",
                {
                    "folder": str(root / "private-folder"),
                    "status": "completed",
                    "items": {
                        "private/image.jpg": {
                            "time": "2026-08-14T12:00:00",
                            "status": "success",
                            "detail": "private caption text",
                        }
                    },
                },
            )

            backup = core.create_app_backup(root / "backup.zip", data_root)
            with zipfile.ZipFile(backup) as archive:
                names = set(archive.namelist())
                self.assertIn("data/settings.json", names)
                self.assertIn("data/projects/project-1/state.json", names)
                self.assertNotIn("data/credentials.bin", names)
                backup_settings = json.loads(
                    archive.read("data/settings.json").decode("utf-8")
                )
                self.assertEqual("<redacted>", backup_settings["api_key"])

            diagnostics = core.create_diagnostic_bundle(
                root / "diagnostics.zip", data_root
            )
            with zipfile.ZipFile(diagnostics) as archive:
                settings = json.loads(
                    archive.read("settings-redacted.json").decode("utf-8")
                )
                state = json.loads(
                    archive.read(
                        "projects/project-1/state-redacted.json"
                    ).decode("utf-8")
                )
            self.assertEqual("<redacted>", settings["api_key"])
            self.assertEqual("private-folder", state["folder"])
            item = next(iter(state["items"].values()))
            self.assertEqual("<omitted>", item["detail"])
            self.assertNotIn("private caption text", json.dumps(state))

    def test_video_batch_uses_worker_preflight_and_closes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_path = root / "clip.mp4"
            video_path.write_bytes(b"small-video")
            probes = []
            closed = []

            class FakeWorker:
                def health(self):
                    return {"capabilities": {"probe": True}}

                def probe(self, path):
                    probes.append(path)
                    return {"video_streams": [{"codec_name": "h264"}]}

                def close(self):
                    closed.append(True)

            def factory(_roots):
                return FakeWorker()

            def sender(method, url, **kwargs):
                return FakeResponse(
                    payload={"choices": [{"message": {"content": "video caption"}}]}
                )

            with mock.patch.object(core, "app_data_dir", return_value=root / "appdata"):
                runner = core.BatchRunner(
                    lambda kind, payload: None,
                    core.HttpTransport(sender),
                    factory,
                )
                summary = runner.run(
                    root,
                    "video",
                    "prompt",
                    "seed-2.1-pro",
                    "key",
                    video_preflight=True,
                )
            self.assertEqual(1, summary.success)
            self.assertEqual([video_path], probes)
            self.assertTrue(closed)

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
