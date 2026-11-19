from datetime import UTC, datetime
from uuid import uuid7

from src.application.schemas import task_warning
from src.application.services.task import TaskService


def _task(**values) -> dict:
    now = datetime.now(UTC)
    return {
        "id": str(uuid7()),
        "batch_id": None,
        "status": "running",
        "stage": "uploading_tg",
        "priority": "normal",
        "path": "tcloud/x/a.mp4",
        "bytes_done": 0,
        "bytes_total": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        **values,
    }


def test_warnings_render_codes_and_legacy_text():
    view = TaskService.render(
        _task(
            warnings=[
                task_warning("video_may_show_as_file", "text", mime="video/webm"),
                "old plain warning",
            ]
        ),
        None,
    )
    assert view.warnings == ["text", "old plain warning"]
    assert [item.model_dump() for item in view.warning_details] == [
        {
            "code": "video_may_show_as_file",
            "params": {"mime": "video/webm"},
            "message": "text",
        },
        {"code": "other", "params": {}, "message": "old plain warning"},
    ]


def test_waiting_renders_code_and_params():
    queued = TaskService.render(
        _task(waiting={"reason": "queue:uploads.telegram"}), None
    )
    assert (queued.waiting.code, queued.waiting.params) == (
        "queue",
        {"queue": "uploads.telegram"},
    )
    flood = TaskService.render(
        _task(waiting={"reason": "flood_wait", "until": 0}), None
    )
    assert (flood.waiting.code, flood.waiting.params) == ("flood_wait", {})


def test_step_progress_only_for_measured_running_steps():
    def render(**values):
        return TaskService.render(_task(**values), None).step_progress

    assert render(step="sending", bytes_done=50, bytes_total=200) == 0.25
    assert render(step="scanning", bytes_done=200, bytes_total=200) == 1
    assert render(step="downloading", bytes_done=50, bytes_total=None) is None
    assert render(step="finishing", bytes_done=200, bytes_total=200) is None
    assert render(step="queued_for_telegram", bytes_total=200) is None
    assert render(step=None, bytes_done=50, bytes_total=200) is None
    assert (
        render(status="done", step="sending", bytes_done=200, bytes_total=200) is None
    )
