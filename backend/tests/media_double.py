"""只在测试使用的持久假提供方账本；每一次 submit 都追加，故能发现盲目重发。"""

import io
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from PIL import Image

from omniflow.media_provider import Accepted, Download, Observation, SubmissionRejected


class FakeMediaProvider:
    reference_editing_enabled = True

    def __init__(self, path):
        self.path = Path(path)
        self.mode = "ok"
        self.gate = None
        self.before_submit = None
        self.downloads = 0
        self.polls = []
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS submissions(id TEXT, task TEXT, inputs TEXT)"
            )
            connection.commit()

    def check(self, kind):
        if self.gate:
            self.gate(kind)

    def submit(self, task, inputs):
        if self.before_submit:
            self.before_submit(task, inputs)
        if self.mode == "reject":
            raise SubmissionRejected("synthetic-secret-not-for-output")
        identity = str(uuid4())
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                "INSERT INTO submissions VALUES (?,?,?)",
                (identity, json.dumps(task), json.dumps(inputs)),
            )
            connection.commit()
        if self.mode == "lost_response":
            raise TimeoutError("synthetic-secret-not-for-output")
        if self.mode == "crash_after_accept":
            raise SystemExit(77)
        if self.mode == "hard_crash_after_accept":
            os._exit(77)
        return Accepted(identity)

    def records(self):
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT * FROM submissions").fetchall()

    def poll(self, task_id):
        self.polls.append(task_id)
        if self.mode == "poll_error":
            raise TimeoutError("synthetic-secret-not-for-output")
        assert task_id in [r[0] for r in self.records()]
        if self.mode == "provider_failed":
            return Observation("failed")
        if self.mode == "pending":
            return Observation("running")
        return Observation("completed", "synthetic-result:" + task_id)

    def download(self, result_key, max_bytes):
        self.downloads += 1
        if self.mode == "download_error":
            raise TimeoutError("synthetic-secret-not-for-output")
        if self.mode == "bad_media":
            return Download(b"not an image or video", "image/png")
        identity = result_key.removeprefix("synthetic-result:")
        task = json.loads(next(r[1] for r in self.records() if r[0] == identity))
        if task["kind"] == "image":
            output = io.BytesIO()
            with Image.new("RGB", (12, 8), "blue") as image:
                image.save(output, "PNG")
            return Download(output.getvalue(), "image/png")
        return Download(
            (Path(__file__).parent / "fixtures/synthetic.mp4").read_bytes(), "video/mp4"
        )
