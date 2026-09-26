"""Tests for ReviewStore and review endpoints."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens import api_server
from deepfake_lens.reviews import ReviewStore, get_default_review_store

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None


class ReviewStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_file = Path(self.temp_dir.name) / "reviews.json"
        self.store = ReviewStore(self.store_file)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_and_get_review(self) -> None:
        res = self.store.save_review("photo1.jpg", {
            "star": True,
            "note": "Forensic anomaly around chin and collar",
            "verdict": "synthetic",
            "analyst_id": "examiner_01",
            "marks": [{"x": 12, "y": 34, "w": 50, "h": 50, "label": "boundary artifact"}],
        })
        self.assertEqual(res["artifact_id"], "photo1.jpg")
        self.assertTrue(res["star"])
        self.assertEqual(res["verdict"], "synthetic")
        self.assertIn("updated_at", res)

        fetched = self.store.get_review("photo1.jpg")
        self.assertIsNotNone(fetched)
        assert fetched is not None
        self.assertEqual(fetched["note"], "Forensic anomaly around chin and collar")
        self.assertEqual(len(fetched["marks"]), 1)

    def test_update_existing_review(self) -> None:
        self.store.save_review("test.png", {"star": True, "note": "initial"})
        self.store.save_review("test.png", {"note": "updated note"})
        fetched = self.store.get_review("test.png")
        assert fetched is not None
        self.assertTrue(fetched["star"])
        self.assertEqual(fetched["note"], "updated note")

    def test_delete_review(self) -> None:
        self.store.save_review("del.jpg", {"star": True})
        self.assertTrue(self.store.delete_review("del.jpg"))
        self.assertIsNone(self.store.get_review("del.jpg"))

    def test_list_reviews(self) -> None:
        self.store.save_review("a.png", {"star": True})
        self.store.save_review("b.png", {"note": "check"})
        all_revs = self.store.list_reviews()
        self.assertEqual(len(all_revs), 2)
        self.assertIn("a.png", all_revs)
        self.assertIn("b.png", all_revs)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
class ReviewApiEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_file = Path(self.temp_dir.name) / "api_reviews.json"
        get_default_review_store(self.store_file)

        from fastapi.testclient import TestClient
        self.client = TestClient(api_server.create_app())

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_put_and_get_artifact_review(self) -> None:
        art_id = "sample/evidence_01.png"
        put_res = self.client.put(
            f"/api/artifacts/{art_id}/review",
            headers={"host": "localhost", "X-Deepfake-Lens-Client": "web-gui"},
            json={"star": True, "note": "Blur pattern mismatch", "verdict": "synthetic"},
        )
        self.assertEqual(put_res.status_code, 200)
        body = put_res.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["review"]["verdict"], "synthetic")

        get_res = self.client.get(
            f"/api/artifacts/{art_id}/review",
            headers={"host": "localhost"},
        )
        self.assertEqual(get_res.status_code, 200)
        get_body = get_res.json()
        self.assertTrue(get_body["review"]["star"])
        self.assertEqual(get_body["review"]["note"], "Blur pattern mismatch")

    def test_get_and_post_review_query_param(self) -> None:
        post_res = self.client.post(
            "/api/review",
            headers={"host": "localhost", "X-Deepfake-Lens-Client": "web-gui"},
            json={"artifact_id": "test_query.jpg", "star": True, "note": "Query test"},
        )
        self.assertEqual(post_res.status_code, 200)

        get_res = self.client.get(
            "/api/review?path=test_query.jpg",
            headers={"host": "localhost"},
        )
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()["review"]["note"], "Query test")

    def test_list_all_reviews_endpoint(self) -> None:
        self.client.put(
            "/api/artifacts/item1.mp4/review",
            headers={"host": "localhost", "X-Deepfake-Lens-Client": "web-gui"},
            json={"star": True},
        )
        res = self.client.get("/api/reviews", headers={"host": "localhost"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("item1.mp4", data["reviews"])
