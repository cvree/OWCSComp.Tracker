#!/usr/bin/env python3
"""
test_desktop_website.py — publishing to the live site.

The point of this module is that "published" stops meaning "regenerated a
file inside the installed application" and starts meaning "other people can
see it". That makes it the one part of the desktop app that reaches outside
the machine, so the properties worth pinning are the ones that protect the
public site from this application:

  * the demo fixture can never be uploaded — its assignment shape is
    `window.OWCS_PUBLIC || {…}`, designed to yield to real data, and putting
    that live would replace the site with sample data that looks real;
  * an upload is VERIFIED by reading the file back, so a success message
    means something was observed rather than assumed;
  * the token never appears in any returned payload, including error paths;
  * every attempt is recorded, including the refused ones.

No network: `publish()` takes an opener, so every path here runs against a
scripted GitHub.

Run: python3 pipeline/test_desktop_website.py
"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "desktop"))

from owcs_desktop import credentials, paths, website  # noqa: E402
from owcs_desktop.settings import Settings  # noqa: E402

TOKEN = "ghp_ThisTokenMustNeverBeReturnedToAnyone0000"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FakeGitHub:
    """A repository that remembers what was PUT into it."""

    def __init__(self, *, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.calls: list[tuple[str, str]] = []
        self.seen_auth: list[str] = []
        #: Set to an HTTPError to make the next PUT fail.
        self.fail_put: urllib.error.HTTPError | None = None
        #: Set to make a read-back return something else than what was sent.
        self.corrupt_readback: str | None = None

    def __call__(self, request, timeout=None):
        method = request.get_method()
        url = request.full_url
        self.seen_auth.append(request.headers.get("Authorization", ""))
        path = url.split("/contents/", 1)[1].split("?", 1)[0]
        self.calls.append((method, path))

        if method == "PUT":
            if self.fail_put is not None:
                raise self.fail_put
            body = json.loads(request.data.decode("utf-8"))
            self.files[path] = base64.b64decode(body["content"]).decode("utf-8")
            return FakeResponse(json.dumps(
                {"commit": {"sha": "c0ffee1234"},
                 "content": {"sha": "blob999"}}).encode("utf-8"))

        if path not in self.files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {},
                                         io.BytesIO(b'{"message":"Not Found"}'))
        content = self.corrupt_readback if self.corrupt_readback is not None \
            else self.files[path]
        return FakeResponse(json.dumps({
            "sha": "blob-" + str(len(self.files[path])),
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }).encode("utf-8"))


REAL_PUBLIC = ('/* generated */\nwindow.OWCS_PUBLIC = {\n  "matches": []\n};\n')
FIXTURE_PUBLIC = ('window.OWCS_PUBLIC = window.OWCS_PUBLIC || {\n'
                  '  "matches": []\n};\n')
REAL_DATA = 'window.OWCS_DATA = {"matches": []};\n'


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="owcs-test-site-")
        self._old_home = os.environ.get(paths.HOME_ENV)
        self._old_app = os.environ.get("OWCS_APP_ROOT")
        os.environ[paths.HOME_ENV] = os.path.join(self._tmp.name, "home")
        self.app = os.path.join(self._tmp.name, "app")
        os.environ["OWCS_APP_ROOT"] = self.app
        paths.ensure_layout()
        self.write_export(REAL_PUBLIC, REAL_DATA)
        Settings().update({"publishRepo": "owner/site",
                           "publishBranch": "main"})

    def tearDown(self) -> None:
        for key, old in ((paths.HOME_ENV, self._old_home),
                         ("OWCS_APP_ROOT", self._old_app)):
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        self._tmp.cleanup()

    def write_export(self, public: str | None, data: str | None) -> None:
        for rel, body in (("assets/data/public_data.v1.js", public),
                          ("assets/js/data.js", data)):
            full = os.path.join(self.app, *rel.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if body is None:
                if os.path.exists(full):
                    os.unlink(full)
                continue
            with open(full, "w", encoding="utf-8") as handle:
                handle.write(body)

    def with_token(self) -> None:
        credentials.CredentialVault().set(website.TOKEN_KEY, TOKEN)


# ------------------------------------------------------------ local checks
class TestValidateLocal(Base):
    def test_a_real_export_validates(self):
        result = website.validate_local()
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(len(result["files"]), 2)

    def test_the_demo_fixture_is_refused(self):
        """The fixture assigns with `|| {…}` so it yields to real data.

        Uploading it would put sample matches on the live site looking
        exactly like real ones. This is the single most damaging thing this
        module could do, so it is refused by name.
        """
        self.write_export(FIXTURE_PUBLIC, REAL_DATA)
        result = website.validate_local()
        self.assertFalse(result["ok"])
        self.assertTrue(any("fixture" in p for p in result["problems"]),
                        result["problems"])

    def test_a_missing_export_is_a_problem_not_a_crash(self):
        self.write_export(None, REAL_DATA)
        result = website.validate_local()
        self.assertFalse(result["ok"])
        self.assertTrue(any("generated" in p for p in result["problems"]))

    def test_an_empty_export_is_refused(self):
        self.write_export("   \n", REAL_DATA)
        result = website.validate_local()
        self.assertFalse(result["ok"])

    def test_an_export_missing_its_global_is_refused(self):
        self.write_export("var something = 1;\n", REAL_DATA)
        result = website.validate_local()
        self.assertFalse(result["ok"])


# --------------------------------------------------------------- readiness
class TestDescribe(Base):
    def test_without_a_token_it_is_not_ready_and_says_why(self):
        state = website.describe()
        self.assertFalse(state["ready"])
        self.assertTrue(any("token" in b for b in state["blockers"]))

    def test_with_a_token_and_a_good_export_it_is_ready(self):
        self.with_token()
        state = website.describe()
        self.assertTrue(state["ready"], state["blockers"])

    def test_an_unset_repository_is_named_as_the_blocker(self):
        self.with_token()
        Settings().update({"publishRepo": ""})
        state = website.describe()
        self.assertFalse(state["ready"])
        self.assertTrue(any("repository" in b for b in state["blockers"]))

    def test_describe_never_returns_the_token(self):
        self.with_token()
        self.assertNotIn(TOKEN, json.dumps(website.describe(), default=str))
        self.assertTrue(website.describe()["hasToken"])


# ---------------------------------------------------------------- publish
class TestPublish(Base):
    def test_it_uploads_both_files_and_reports_the_commit(self):
        self.with_token()
        hub = FakeGitHub()
        result = website.publish(by="Connor", opener=hub)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            {f["path"] for f in result["files"]},
            {"assets/data/public_data.v1.js", "assets/js/data.js"})
        self.assertEqual(hub.files["assets/data/public_data.v1.js"],
                         REAL_PUBLIC)
        self.assertTrue(all(f["commit"] for f in result["files"]))

    def test_it_updates_an_existing_file_by_sha(self):
        """Without the current blob sha GitHub treats a PUT as a create and
        rejects it for a path that already exists."""
        self.with_token()
        hub = FakeGitHub(files={"assets/data/public_data.v1.js": "old",
                                "assets/js/data.js": "old"})
        result = website.publish(opener=hub)
        self.assertTrue(result["ok"], result)
        self.assertIn(("PUT", "assets/data/public_data.v1.js"), hub.calls)
        self.assertEqual(hub.files["assets/js/data.js"], REAL_DATA)

    def test_identical_content_is_not_re_uploaded(self):
        self.with_token()
        hub = FakeGitHub(files={"assets/data/public_data.v1.js": REAL_PUBLIC,
                                "assets/js/data.js": REAL_DATA})
        result = website.publish(opener=hub)
        self.assertTrue(result["ok"], result)
        self.assertEqual([c for c in hub.calls if c[0] == "PUT"], [])
        self.assertTrue(all(f["status"] == "unchanged"
                            for f in result["files"]))

    def test_a_publish_is_verified_by_reading_it_back(self):
        """A success that was never observed is not a success."""
        self.with_token()
        hub = FakeGitHub()
        hub.corrupt_readback = "something else entirely"
        result = website.publish(opener=hub)
        self.assertFalse(result["ok"])
        self.assertIn("does not match", result["error"])

    def test_without_a_token_nothing_is_sent(self):
        hub = FakeGitHub()
        result = website.publish(opener=hub)
        self.assertFalse(result["ok"])
        self.assertEqual(hub.calls, [], "it talked to GitHub with no token")

    def test_the_fixture_is_never_uploaded(self):
        self.with_token()
        self.write_export(FIXTURE_PUBLIC, REAL_DATA)
        hub = FakeGitHub()
        result = website.publish(opener=hub)
        self.assertFalse(result["ok"])
        self.assertEqual(hub.calls, [],
                         "the demo fixture reached the live site")

    def test_the_token_is_sent_as_a_bearer_and_never_returned(self):
        self.with_token()
        hub = FakeGitHub()
        result = website.publish(opener=hub)
        self.assertTrue(any(TOKEN in a for a in hub.seen_auth),
                        "the token was not actually used to authenticate")
        self.assertNotIn(TOKEN, json.dumps(result, default=str))

    def test_an_http_error_is_explained_not_raised(self):
        self.with_token()
        for code, expect in ((401, "token"), (403, "Contents:write"),
                             (404, "does not exist"), (409, "changed")):
            with self.subTest(code=code):
                hub = FakeGitHub()
                hub.fail_put = urllib.error.HTTPError(
                    "https://api.github.com/x", code, "err", {},
                    io.BytesIO(b'{"message":"nope"}'))
                result = website.publish(opener=hub)
                self.assertFalse(result["ok"])
                self.assertIn(expect, result["error"])
                self.assertNotIn(TOKEN, json.dumps(result, default=str))

    def test_a_network_failure_is_reported_not_raised(self):
        self.with_token()

        def boom(request, timeout=None):
            raise urllib.error.URLError("no route to host")

        result = website.publish(opener=boom)
        self.assertFalse(result["ok"])
        self.assertIn("URLError", result["error"])


# ----------------------------------------------------------------- audit
class TestAudit(Base):
    def test_a_successful_publish_is_recorded(self):
        self.with_token()
        website.publish(by="Connor", opener=FakeGitHub())
        entries = website.history()
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0]["ok"])
        self.assertEqual(entries[0]["by"], "Connor")
        self.assertEqual(entries[0]["repo"], "owner/site")

    def test_a_refused_publish_is_recorded_too(self):
        """A publish that was refused is exactly what someone looks up later."""
        website.publish(opener=FakeGitHub())          # no token
        entries = website.history()
        self.assertEqual(len(entries), 1)
        self.assertFalse(entries[0]["ok"])
        self.assertIn("token", entries[0]["error"])

    def test_the_history_never_contains_the_token(self):
        self.with_token()
        website.publish(opener=FakeGitHub())
        with open(website.audit_path(), "r", encoding="utf-8") as handle:
            self.assertNotIn(TOKEN, handle.read())

    def test_an_unsigned_publish_records_anonymous(self):
        self.with_token()
        website.publish(opener=FakeGitHub())
        self.assertEqual(website.history()[0]["by"], "anonymous")

    def test_a_corrupt_history_file_does_not_break_publishing(self):
        self.with_token()
        os.makedirs(os.path.dirname(website.audit_path()), exist_ok=True)
        with open(website.audit_path(), "w", encoding="utf-8") as handle:
            handle.write("{ this is not json")
        result = website.publish(opener=FakeGitHub())
        self.assertTrue(result["ok"], result)
        self.assertEqual(website.history()[0]["ok"], True)


# ------------------------------------------------------------ the API route
class TestRoute(Base):
    def test_the_publish_status_route_includes_the_live_site(self):
        from owcs_desktop import webapi
        _code, payload = webapi.handle_get("/api/desktop/publish")
        self.assertIn("site", payload)
        self.assertIn("blockers", payload["site"])

    def test_the_site_route_exists_and_does_not_leak_the_token(self):
        from owcs_desktop import webapi
        self.with_token()
        code, payload = webapi.handle_post("/api/desktop/publish/site", {})
        self.assertIn(code, (200, 409), payload)
        self.assertNotIn(TOKEN, json.dumps(payload, default=str))


if __name__ == "__main__":
    unittest.main(verbosity=2)
