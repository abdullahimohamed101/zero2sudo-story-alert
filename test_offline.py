import os
import sqlite3
import tempfile
from unittest.mock import patch

import main


def test_redirect_unwrap():
    wrapped = "https://l.instagram.com/?u=https%3A%2F%2Fexample.com%2Fjobs%2F123&e=abc"
    assert main.normalize_external_url(wrapped) == "https://example.com/jobs/123"


def test_instagram_filtered():
    assert main.normalize_external_url("https://www.instagram.com/foo/") is None


def test_direct_external():
    assert main.normalize_external_url("https://boards.greenhouse.io/example/jobs/123") == \
        "https://boards.greenhouse.io/example/jobs/123"


def test_db_dedupe():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = main.DATA_DIR
        old_db = main.DB_PATH
        main.DATA_DIR = main.Path(tmp)
        main.DB_PATH = main.DATA_DIR / "stories.db"

        main.init_db()
        url = "https://example.com/job/1"
        assert not main.already_seen(url)
        main.mark_seen(url)
        assert main.already_seen(url)

        main.DATA_DIR = old_dir
        main.DB_PATH = old_db


if __name__ == "__main__":
    test_redirect_unwrap()
    test_instagram_filtered()
    test_direct_external()
    test_db_dedupe()
    print("All offline tests passed.")
