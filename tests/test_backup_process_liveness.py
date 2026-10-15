"""Recovery must inspect process liveness without signalling a live worker."""

import os
import subprocess
import sys

import pytest

from agent_shell.server import project_backup


@pytest.mark.skipif(os.name != "nt", reason="Windows process query semantics")
def test_stage_liveness_query_does_not_signal_or_stop_another_process(monkeypatch):
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("must not signal on Windows"))
        payload = {"process_id": worker.pid, "owner_token": "not-this-process"}
        assert project_backup._stage_process_is_active(payload)
        assert worker.poll() is None
        worker.terminate()
        worker.wait(timeout=5)
        assert not project_backup._stage_process_is_active(payload)
    finally:
        if worker.poll() is None:
            worker.terminate()
        worker.wait(timeout=5)
