import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bootstrap  # noqa: F401  — isolates data dirs, adds src/ to sys.path

from fastapi.testclient import TestClient

from store import Store
from web.server import create_app
from web.services import AppServices

TOKEN = "t" * 43
HOST = "127.0.0.1:5555"


class FakeManager:
    def __init__(self):
        self.is_running = False
        self.current = None
        self.started = []
        self.cancelled = 0
        self.queue = None
        self.subscribed = True

    def start(self, trigger="manual"):
        from runner import NoSearchSettings, RunInProgress
        if self.is_running:
            raise RunInProgress("busy")
        if getattr(self, "no_settings", False):
            raise NoSearchSettings("none")
        self.started.append(trigger)
        return 42

    def cancel(self):
        self.cancelled += 1
        return self.is_running

    def subscribe(self, maxsize=1000):
        import queue
        self.queue = queue.Queue()
        return self.queue

    def unsubscribe(self, q):
        self.subscribed = False

    def is_subscribed(self, q):
        return self.subscribed


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "t.db"
        self.config = {"keywords": ["Java"], "locations": ["jo"], "sources": ["bayt"]}
        self.app_settings = {"language": "ar", "theme": "auto", "auto_search": {"enabled": True, "hours": 6},
                             "onboarding_done": True, "update_check": True, "last_update_check": None,
                             "latest_version": None, "last_posts_run": None}
        self.opened_urls, self.opened_paths, self.launched = [], [], []
        self.release = {"tag_name": "v1.0.0"}
        self.manager = FakeManager()
        (root / "profile").mkdir()

        def save_app(patch):
            import settings
            errors = {k: "unknown" for k in patch if k not in self.app_settings}
            if errors:
                raise settings.SettingsError(errors)
            self.app_settings.update(patch)
            return dict(self.app_settings)

        self.services = AppServices(
            manager=self.manager,
            store_factory=lambda: Store(self.db),
            load_config=lambda: self.config,
            save_config=lambda data: self.config.update(data),
            load_app_settings=lambda: dict(self.app_settings),
            save_app_settings=save_app,
            base_countries=lambda: [{"id": "remote", "label": "Remote"}, {"id": "jo", "label": "Jordan"},
                                    {"id": "sa", "label": "Saudi Arabia"}],
            find_chrome=lambda: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            linkedin_profile_dir=lambda: root / "profile",
            data_dir=lambda: root,
            log_path=lambda: root / "hawkeye.log",
            open_url=self.opened_urls.append,
            open_path=self.opened_paths.append,
            launch=self.launched.append,
            fetch_latest_release=lambda: self.release,
            version="1.0.0",
            clock=lambda: 1_000_000.0,
            os_name="macOS 15",
            home=str(root / "home" / "sam"),
            user="sam",
        )
        self.client = TestClient(create_app(token=TOKEN, allowed_hosts={HOST}, services=self.services),
                                 base_url=f"http://{HOST}")
        with Store(self.db) as store:
            run_id = store.start_run(self.config)
            store.add_jobs(run_id, [{"title": "Java Developer", "url": "https://a.com/1", "description": "Acme",
                                     "source": "Bayt", "location": "jo", "published": ""}])
            store.record_query(run_id, {"type": "query_done", "index": 0, "source": "bayt", "keyword": "Java",
                                        "location_id": "jo", "found": 1, "new": 1})
            store.finish_run(run_id, "done", 1, 1)
            self.run_id = run_id

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, path, **kwargs):
        return self.client.get(path, headers={"X-Hawkeye-Token": TOKEN}, **kwargs)


class GuardTests(ApiTestCase):
    def test_api_without_token_is_forbidden(self):
        self.assertEqual(self.client.get("/api/state").status_code, 403)
        self.assertEqual(self.client.get("/api/state", headers={"X-Hawkeye-Token": "wrong"}).status_code, 403)

    def test_foreign_host_is_forbidden_even_for_static(self):
        evil = TestClient(create_app(token=TOKEN, allowed_hosts={HOST}, services=self.services),
                          base_url="http://evil.example:5555")
        self.assertEqual(evil.get("/").status_code, 403)
        self.assertEqual(evil.get("/api/state", headers={"X-Hawkeye-Token": TOKEN}).status_code, 403)

    def test_token_query_param_only_for_events(self):
        self.assertEqual(self.client.get(f"/api/state?token={TOKEN}").status_code, 403)

    def test_static_index_served_without_token(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Hawkeye Jobs", response.text)


class ReadTests(ApiTestCase):
    def test_state(self):
        state = self.get("/api/state").json()
        self.assertEqual(state["version"], "1.0.0")
        self.assertTrue(state["onboarding_done"])
        self.assertEqual(state["language"], "ar")
        self.assertTrue(state["chrome"]["found"])
        self.assertFalse(state["linkedin_profile"])  # folder exists but is empty
        self.assertIsNone(state["run"])
        self.assertEqual(state["job_counts"]["new"], 1)
        self.assertTrue(state["has_settings"])
        self.assertEqual(state["update"], {"available": False, "latest": None})

    def test_settings_and_app_settings(self):
        self.assertEqual(self.get("/api/settings").json(), self.config)
        self.assertEqual(self.get("/api/app-settings").json()["language"], "ar")

    def test_settings_when_none_saved(self):
        self.config = None
        self.services.load_config = lambda: None
        self.assertEqual(self.get("/api/settings").json(), {"keywords": [], "locations": [], "sources": []})

    def test_countries_localized(self):
        rows = self.get("/api/countries?lang=ar").json()
        self.assertEqual(rows[0], {"id": "remote", "label": "عن بُعد"})
        self.assertIn({"id": "jo", "label": "الأردن"}, rows)
        self.assertEqual(self.get("/api/countries?lang=xx").status_code, 422)

    def test_jobs_list_and_filters(self):
        body = self.get("/api/jobs").json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["jobs"][0]["title"], "Java Developer")
        self.assertEqual(self.get("/api/jobs?status=saved").json()["total"], 0)
        self.assertEqual(self.get("/api/jobs?status=bogus").status_code, 422)
        self.assertEqual(self.get("/api/jobs?limit=500").status_code, 422)

    def test_runs(self):
        runs = self.get("/api/runs").json()["runs"]
        self.assertEqual(runs[0]["id"], self.run_id)
        details = self.get(f"/api/runs/{self.run_id}").json()
        self.assertEqual(details["queries"][0]["status"], "done")
        self.assertEqual(self.get("/api/runs/999").status_code, 404)


class WriteTests(ApiTestCase):
    def send(self, method, path, json=None):
        return self.client.request(method, path, headers={"X-Hawkeye-Token": TOKEN}, json=json)

    def test_put_settings_validates_and_saves(self):
        ok = self.send("PUT", "/api/settings", {"keywords": [" Java "], "locations": ["jo", "remote"],
                                                "sources": ["bayt"]})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.config["keywords"], ["Java"])
        bad = self.send("PUT", "/api/settings", {"keywords": [], "locations": ["xx"], "sources": []})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.json()["errors"], {"keywords": "required", "locations": "invalid",
                                                "sources": "required"})

    def test_patch_app_settings_only_user_keys(self):
        ok = self.send("PATCH", "/api/app-settings", {"language": "en"})
        self.assertEqual((ok.status_code, ok.json()["language"]), (200, "en"))
        internal = self.send("PATCH", "/api/app-settings", {"last_posts_run": "2026-01-01T00:00:00Z"})
        self.assertEqual(internal.status_code, 422)
        self.assertEqual(internal.json()["errors"], {"last_posts_run": "unknown"})

    def test_patch_job_status(self):
        from store import job_id
        jid = job_id("https://a.com/1")
        self.assertEqual(self.send("PATCH", f"/api/jobs/{jid}", {"status": "saved"}).status_code, 200)
        self.assertEqual(self.get("/api/jobs?status=saved").json()["total"], 1)
        self.assertEqual(self.send("PATCH", "/api/jobs/nope", {"status": "saved"}).status_code, 404)
        self.assertEqual(self.send("PATCH", f"/api/jobs/{jid}", {"status": "archived"}).status_code, 422)

    def test_start_run(self):
        response = self.send("POST", "/api/runs")
        self.assertEqual((response.status_code, response.json()), (202, {"run_id": 42}))
        self.assertEqual(self.manager.started, ["manual"])

    def test_start_run_still_starting_returns_null_id(self):
        self.manager.start = lambda trigger="manual": None
        response = self.send("POST", "/api/runs")
        self.assertEqual((response.status_code, response.json()), (202, {"run_id": None}))

    def test_start_run_conflicts(self):
        self.manager.is_running = True
        self.assertEqual(self.send("POST", "/api/runs").json(), {"error": "run_in_progress"})
        self.assertEqual(self.send("POST", "/api/runs").status_code, 409)
        self.manager.is_running = False
        self.manager.no_settings = True
        response = self.send("POST", "/api/runs")
        self.assertEqual((response.status_code, response.json()), (422, {"error": "no_settings"}))

    def test_cancel(self):
        self.assertEqual(self.send("POST", "/api/runs/current/cancel").status_code, 409)
        self.manager.is_running = True
        self.assertEqual(self.send("POST", "/api/runs/current/cancel").status_code, 202)

    def test_events_stream(self):
        import threading

        def feed():
            import time
            deadline = time.time() + 5
            while self.manager.queue is None and time.time() < deadline:
                time.sleep(0.01)
            self.manager.queue.put({"type": "run_started", "run_id": 1, "total": 2})
            self.manager.queue.put(None)  # app shutting down: stream ends

        threading.Thread(target=feed, daemon=True).start()
        with self.client.stream("GET", f"/api/events?token={TOKEN}") as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            body = "".join(response.iter_text())
        self.assertIn('data: {"type": "run_started", "run_id": 1, "total": 2}', body)
        self.assertFalse(self.manager.subscribed)

    def test_event_stream_generator_unsubscribes_on_close(self):
        from web import api
        q = self.manager.subscribe()
        stream = api._event_stream(self.manager, q)
        self.assertEqual(next(stream), "retry: 3000\n\n")
        stream.close()  # client disconnected
        self.assertFalse(self.manager.subscribed)


if __name__ == "__main__":
    unittest.main()
