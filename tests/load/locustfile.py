import io
import os
import time
from uuid import uuid4

from locust import HttpUser, between, events, task
from locust.env import Environment
from loguru import logger
from PIL import Image

ROOT_LOGIN = os.environ.get("LOAD_ROOT_LOGIN", "load")
ROOT_PASSWORD = os.environ.get("LOAD_ROOT_PASSWORD", "load-password")
MAX_P95_MS = int(os.environ.get("LOAD_MAX_P95_MS", "500"))
MAX_FAIL_RATIO = float(os.environ.get("LOAD_MAX_FAIL_RATIO", "0.01"))
TASK_TIMEOUT = float(os.environ.get("LOAD_TASK_TIMEOUT", "120"))


def _image() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buffer, "JPEG")
    return buffer.getvalue()


IMAGE = _image()


class Uploader(HttpUser):
    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        self.client.post(
            "/api/auth/signin",
            json={"login": ROOT_LOGIN, "password": ROOT_PASSWORD},
            name="setup: sign in",
        ).raise_for_status()
        me = self.client.get("/api/users/me", name="setup: me").json()
        key = self.client.post(
            f"/api/users/{me['id']}/api-keys",
            json={"name": "locust"},
            name="setup: api key",
        ).json()["key"]
        self.client.cookies.clear()
        self.client.headers["X-API-Key"] = key
        storage = self.client.post(
            "/api/storages",
            json={"name": f"load-{uuid4().hex[:6]}"},
            name="setup: storage",
        ).json()
        self.root = f"tcloud/{storage['id']}"

    def _wait(self, task_id: str) -> None:
        deadline = time.monotonic() + TASK_TIMEOUT
        started = time.monotonic()
        while time.monotonic() < deadline:
            status = self.client.get(
                f"/api/tasks/{task_id}", name="/api/tasks/[id]"
            ).json()["status"]
            if status in ("done", "failed", "cancelled"):
                events.request.fire(
                    request_type="TASK",
                    name="upload end-to-end",
                    response_time=(time.monotonic() - started) * 1000,
                    response_length=0,
                    exception=None if status == "done" else RuntimeError("task failed"),
                )
                return
            time.sleep(0.2)
        events.request.fire(
            request_type="TASK",
            name="upload end-to-end",
            response_time=TASK_TIMEOUT * 1000,
            response_length=0,
            exception=TimeoutError("task did not finish"),
        )

    def _start(self, target: str) -> str | None:
        with self.client.post(
            "/api/uploads",
            params={"path": target},
            data=IMAGE,
            name="/api/uploads",
            catch_response=True,
        ) as response:
            if response.status_code == 429:
                response.success()
                time.sleep(response.json()["error"].get("retry_after", 1))
                return None
            return response.json()["id"]

    @task(5)
    def upload_one(self) -> None:
        task_id = self._start(f"{self.root}/single/{uuid4().hex}.jpg")
        if task_id:
            self._wait(task_id)

    @task(2)
    def browse(self) -> None:
        self.client.get("/api/folders", params={"path": self.root}, name="/api/folders")
        self.client.get("/api/storages", name="/api/storages")


@events.quitting.add_listener
def check_thresholds(environment: Environment, **_: object) -> None:
    total = environment.stats.total
    problems = []
    if total.fail_ratio > MAX_FAIL_RATIO:
        problems.append(f"fail ratio {total.fail_ratio:.2%} > {MAX_FAIL_RATIO:.2%}")
    for (name, method), entry in environment.stats.entries.items():
        if method == "TASK" or name.startswith("setup: "):
            continue
        p95 = entry.get_response_time_percentile(0.95) or 0
        if p95 > MAX_P95_MS:
            problems.append(f"{method} {name} p95 {p95} ms > {MAX_P95_MS} ms")
    if problems:
        logger.error("Load test failed: {}", "; ".join(problems))
        environment.process_exit_code = 1
    else:
        logger.info(
            "Load test passed: fail ratio {:.2%}, p95 {} ms",
            total.fail_ratio,
            total.get_response_time_percentile(0.95),
        )
