"""Qt thread adapters around the stable core workers."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

import media_caption_core as core


class BatchWorker(QThread):
    event_received = Signal(str, object)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, kwargs: dict[str, Any]) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.runner = core.BatchRunner(self._on_event)

    def _on_event(self, kind: str, payload: dict[str, Any]) -> None:
        self.event_received.emit(kind, payload)

    def run(self) -> None:
        try:
            self.completed.emit(self.runner.run(**self.kwargs))
        except Exception as error:  # pragma: no cover - provider/runtime dependent
            self.failed.emit(str(error))

    def cancel(self) -> None:
        self.runner.cancel()


class FunctionWorker(QThread):
    """Run a short core operation without blocking the Qt event loop."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, function, *args, **kwargs) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:
        try:
            self.succeeded.emit(self.function(*self.args, **self.kwargs))
        except Exception as error:  # pragma: no cover - network/runtime dependent
            self.failed.emit(str(error))


class ProviderTestWorker(FunctionWorker):
    def __init__(self, provider_key: str, api_key: str, api_endpoint: str = "") -> None:
        super().__init__(
            core.test_provider_connection,
            provider_key,
            api_key,
            api_endpoint=api_endpoint,
        )


class UpdateCheckWorker(FunctionWorker):
    def __init__(self) -> None:
        super().__init__(core.check_latest_release)


class LmStudioInventoryWorker(FunctionWorker):
    def __init__(self, base_url: str) -> None:
        super().__init__(core.list_lmstudio_models, base_url)


class LmStudioLoadWorker(FunctionWorker):
    def __init__(self, base_url: str, model_key: str, profile: str) -> None:
        super().__init__(
            core.load_lmstudio_model,
            base_url,
            model_key,
            load_profile=profile,
        )


class LmStudioUnloadWorker(FunctionWorker):
    def __init__(self, base_url: str, instance_id: str) -> None:
        super().__init__(core.unload_lmstudio_model, base_url, instance_id)
