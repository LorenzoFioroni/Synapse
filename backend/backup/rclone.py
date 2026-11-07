"""Run the server's rclone with an isolated configuration and no shell or inherited remotes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .errors import BackupError


class Rclone:
    def __init__(self, config: Path):
        self.config = config
        self._processes: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._stopped = False

    @property
    def available(self) -> bool:
        return shutil.which("rclone") is not None

    def run(self, *args: str, input: str | None = None, timeout: int = 300) -> str:
        executable = shutil.which("rclone")
        if not executable:
            raise BackupError("Remote backups need rclone on the server. The Synapse Docker image includes it.")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("RCLONE_")}
        command = [
            executable,
            "--config",
            str(self.config),
            "--ask-password=false",
            "--retries",
            "1",
            "--contimeout",
            "15s",
            "--timeout",
            "60s",
            *args,
        ]
        with self._lock:
            if self._stopped:
                raise BackupError("Remote backup work was stopped.")
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env
            )
            self._processes.add(process)
        try:
            stdout, _ = process.communicate(input=input, timeout=timeout)
            if process.returncode:
                # Provider errors may contain credentials, signed URLs or response bodies.
                raise BackupError(
                    "rclone could not complete the operation. Check the connection and account authorization in Settings → Backups."
                )
            return stdout
        except subprocess.TimeoutExpired as e:
            process.kill()
            process.communicate()
            raise BackupError(
                "The remote storage operation timed out. It can be retried from Settings → Backups."
            ) from e
        finally:
            with self._lock:
                self._processes.discard(process)

    def json(self, *args: str) -> list[dict]:
        try:
            result = json.loads(self.run(*args))
            if not isinstance(result, list):
                raise ValueError
            return result
        except ValueError as e:
            raise BackupError("rclone returned an unreadable file listing.") from e

    def start(self) -> None:
        with self._lock:
            self._stopped = False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            processes = tuple(self._processes)
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
