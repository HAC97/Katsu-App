"""Test isolation: each run gets its own throwaway database.

This runs before any test module is imported, so `DATABASE_PATH` is already set
when `config` (and therefore `database`) is imported, no matter the import
order. Without it, tests can silently use the real `stories.db`.
"""
import os
import tempfile
from pathlib import Path

os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp(prefix="katsu-tests-")) / "test.db")
