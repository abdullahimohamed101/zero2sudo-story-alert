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


def test_html_escaped_url():
    assert main.normalize_external_url("https://example.com/job?a=1&amp;b=2") == \
        "https://example.com/job?a=1&b=2"


def test_story_json_link_for_target_account():
    payload = {
        "data": {
            "reels_media": [{
                "user": {"username": "zero2sudo"},
                "items": [{
                    "story_link_stickers": [{
                        "story_link": {
                            "link_url": "https://jobs.example.com/apply/123"
                        }
                    }]
                }],
            }]
        }
    }

    assert main.extract_story_links_from_payload(payload) == {
        "https://jobs.example.com/apply/123"
    }


def test_story_json_ignores_another_account():
    payload = {
        "user": {"username": "someone_else"},
        "story_link": {"link_url": "https://example.com/not-the-target"},
    }

    assert main.extract_story_links_from_payload(payload) == set()


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


def test_email_unseen_links_sends_only_once():
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = main.DATA_DIR
        old_db = main.DB_PATH
        main.DATA_DIR = main.Path(tmp)
        main.DB_PATH = main.DATA_DIR / "stories.db"

        main.init_db()
        url = "https://example.com/simulated-story"

        with patch.object(main, "send_email") as mocked_send:
            assert main.email_unseen_links({url}) == 1
            assert main.email_unseen_links({url}) == 0
            mocked_send.assert_called_once_with(url)

        main.DATA_DIR = old_dir
        main.DB_PATH = old_db


if __name__ == "__main__":
    test_redirect_unwrap()
    test_instagram_filtered()
    test_direct_external()
    test_html_escaped_url()
    test_story_json_link_for_target_account()
    test_story_json_ignores_another_account()
    test_db_dedupe()
    test_email_unseen_links_sends_only_once()
    print("All offline tests passed.")
