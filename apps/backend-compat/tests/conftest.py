"""Finish module-owned workers before monkeypatch teardown and module reload."""
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def finish_backend_workers_before_restore(monkeypatch):
    # Depending on monkeypatch keeps its globals valid until workers finish.
    yield
    deadline = time.monotonic() + 120.0
    while True:
        workers = [thread for thread in threading.enumerate()
                   if thread.name == "atlas-loader"
                   or thread.name.startswith(("risk-sdf-builder-", "mri-reconstruct-"))]
        if not workers:
            return
        for worker in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail("Backend worker did not terminate before fixture teardown: " + worker.name)
            worker.join(timeout=remaining)
