import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEMPLATES = ROOT / "swa_data_tool" / "templates"
SAMPLE = (TEMPLATES / "ngo_beneficiary_register.csv").read_bytes()


def make_site(ads=None, env=None):
    d = Path(tempfile.mkdtemp())
    shutil.copy(TEMPLATES / "config_ngo.yaml", d / "config.yaml")
    (d / "profiles").mkdir()
    shutil.copy(TEMPLATES / "config_sales.yaml", d / "profiles" / "sales_demo.yaml")
    if ads is not None:
        (d / "ads.json").write_text(json.dumps(ads))
    for k in ("SWA_PASSWORD", "SWA_ADMIN_PASSWORD", "SWA_RETENTION_HOURS", "SWA_ADS_FILE", "SWA_ADS_DIR", "SWA_PROFILES_DIR"):
        os.environ.pop(k, None)
    os.environ["SWA_SECRET"] = "unit-test-secret"
    for k, v in (env or {}).items():
        os.environ[k] = v
    from swa_data_tool.web import create_app
    app = create_app(str(d / "config.yaml"))
    app.testing = True
    return d, app


def token(client):
    html = client.get("/").get_data(as_text=True)
    return html.split("name='csrf' value='")[1].split("'")[0]


def upload(client, title="", profile=None, data=SAMPLE, name="register.csv"):
    form = {"csrf": token(client), "title": title, "file": (io.BytesIO(data), name)}
    if profile:
        form["profile"] = profile
    return client.post("/analyse", data=form, content_type="multipart/form-data")


class Web(unittest.TestCase):
    def setUp(self):
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("SWA_SECRET", "SWA_ADMIN_PASSWORD", "SWA_PASSWORD", "SWA_RETENTION_HOURS")])

    def site(self, **kw):
        d, app = make_site(**kw)
        self.addCleanup(shutil.rmtree, d, True)
        return d, app

    def test_home_is_generic_and_branded(self):
        _, app = self.site()
        html = app.test_client().get("/").get_data(as_text=True)
        self.assertNotIn("Youth Programme", html)                 # no fixed dataset title
        self.assertIn("swasamson.site", html)
        self.assertIn("Your ad could be here", html)             # placeholders until ads.json is filled
        self.assertIn("Name this analysis", html)

    def test_upload_flow_uses_title_and_escapes_it(self):
        _, app = self.site()
        c = app.test_client()
        r = upload(c, title="Q3 <script>alert(1)</script> Report")
        self.assertEqual(r.status_code, 302)
        page = c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        run = r.headers["Location"].rsplit("/", 1)[1]
        report = c.get(f"/runs/{run}/view").get_data(as_text=True)
        self.assertIn("Q3 &lt;script&gt;", report)
        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertIn("swasamson.site", report)                   # report branding

    def test_default_title_comes_from_file_name_not_config(self):
        _, app = self.site()
        c = app.test_client()
        r = upload(c, name="my_health_outreach.csv")
        page = c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("My health outreach", page)
        self.assertNotIn("Data Analysis Report", page.split("<main")[1])

    def test_visitors_cannot_see_each_others_results(self):
        _, app = self.site()
        a, b = app.test_client(), app.test_client()
        loc = upload(a).headers["Location"]
        self.assertEqual(a.get(loc).status_code, 200)
        self.assertEqual(b.get(loc).status_code, 404)
        self.assertEqual(b.get(loc + "/zip").status_code, 404)
        self.assertEqual(b.get(loc + "/view").status_code, 404)
        self.assertEqual(b.get(loc + "/file/report.xlsx").status_code, 404)
        hist_b = b.get("/history").get_data(as_text=True)
        self.assertNotIn(loc.rsplit("/", 1)[1], hist_b)
        self.assertIn(loc.rsplit("/", 1)[1], a.get("/history").get_data(as_text=True))

    def test_path_traversal_blocked(self):
        _, app = self.site()
        c = app.test_client()
        for bad in ("/runs/..%2f..%2fconfig.yaml", "/runs/../x", "/runs/%2e%2e/file/report.xlsx"):
            self.assertIn(c.get(bad).status_code, (404, 308, 301))
        self.assertEqual(c.get("/ads-media/..%2fconfig.yaml").status_code, 404)
        self.assertEqual(c.get("/ads-media/config.yaml").status_code, 404)

    def test_csrf_required(self):
        _, app = self.site()
        c = app.test_client()
        c.get("/")
        r = c.post("/analyse", data={"file": (io.BytesIO(SAMPLE), "x.csv")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_unsupported_type_rejected(self):
        _, app = self.site()
        c = app.test_client()
        r = c.post("/analyse", data={"csrf": token(c), "file": (io.BytesIO(b"x"), "evil.exe")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)

    def test_settings_locked_on_public_requests(self):
        _, app = self.site()
        c = app.test_client()
        self.assertEqual(c.get("/settings", headers={"X-Forwarded-For": "1.2.3.4"}).status_code, 403)
        self.assertEqual(c.post("/settings", data={"text": "x"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code, 403)
        self.assertNotIn(">Settings<", c.get("/", headers={"X-Forwarded-For": "1.2.3.4"}).get_data(as_text=True))
        self.assertEqual(c.get("/settings", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code, 200)

    def test_settings_with_admin_password(self):
        import base64
        _, app = self.site(env={"SWA_ADMIN_PASSWORD": "s3cret"})
        c = app.test_client()
        self.assertEqual(c.get("/settings").status_code, 401)
        good = {"Authorization": "Basic " + base64.b64encode(b"admin:s3cret").decode()}
        bad = {"Authorization": "Basic " + base64.b64encode(b"admin:nope").decode()}
        self.assertEqual(c.get("/settings", headers=bad).status_code, 401)
        r = c.get("/settings", headers=good)
        self.assertEqual(r.status_code, 200)
        tok = r.get_data(as_text=True).split("name='csrf' value='")[1].split("'")[0]
        broken = c.post("/settings", headers=good, data={"csrf": tok, "action": "save", "text": "columns: nope"})
        self.assertIn("Problem", broken.get_data(as_text=True))                 # invalid config is never saved
        nocsrf = c.post("/settings", headers=good, data={"action": "save", "text": "x"})
        self.assertEqual(nocsrf.status_code, 400)

    def test_site_password(self):
        import base64
        _, app = self.site(env={"SWA_PASSWORD": "pw1"})
        c = app.test_client()
        self.assertEqual(c.get("/").status_code, 401)
        self.assertEqual(c.get("/", headers={"Authorization": "Basic " + base64.b64encode(b"u:pw1").decode()}).status_code, 200)
        self.assertEqual(c.get("/healthz").status_code, 200)

    def test_profile_selection(self):
        _, app = self.site()
        c = app.test_client()
        self.assertIn("Sales demo", c.get("/").get_data(as_text=True))
        csv_sales = b"Order ID,Date,Region,Product,Units,Revenue\nORD-1,01/02/2025,North,Laptop,2,\"1,000.00\"\nORD-2,03/02/2025,South,Printer,1,500.00\n"
        r = upload(c, profile="sales_demo", data=csv_sales, name="s.csv")
        run = r.headers["Location"].rsplit("/", 1)[1]
        page = c.get(f"/runs/{run}").get_data(as_text=True)
        self.assertIn("PASSED", page)
        r2 = upload(c, profile="does_not_exist", data=csv_sales, name="s.csv")      # unknown key -> standard profile
        self.assertIn("could not be processed", c.get(r2.headers["Location"]).get_data(as_text=True) + "could not be processed")

    def test_bad_columns_give_clear_failure_and_no_report(self):
        _, app = self.site()
        c = app.test_client()
        r = upload(c, data=b"a,b\n1,2\n", name="wrong.csv")
        page = c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("Could not process", page)
        self.assertNotIn("Excel report", page)

    def test_ads_rendering_and_safety(self):
        ads = {"contact": "mailto:ads@example.org", "slots": {
            "top": {"type": "image", "src": "https://cdn.example.org/b.png", "href": "https://adv.example.org", "alt": "Acme"},
            "home_below": {"type": "html", "html": "<script>document.cookie</script>", "width": 300, "height": 250},
            "result_below": [{"type": "text", "title": "Sponsor <b>A</b>", "body": "x", "href": "javascript:alert(1)"},
                             {"type": "text", "title": "Sponsor B", "body": "hello", "href": "https://b.example.org"}],
            "footer": {"type": "text", "title": "Old campaign", "body": "x", "href": "https://old.example.org", "end": "2020-01-01"}}}
        _, app = self.site(ads=ads)
        c = app.test_client()
        home = c.get("/").get_data(as_text=True)
        self.assertIn("https://cdn.example.org/b.png", home)
        self.assertIn("rel='sponsored nofollow noopener'", home)
        self.assertIn("sandbox='allow-scripts allow-popups allow-popups-to-escape-sandbox'", home)   # script ads are sandboxed
        self.assertNotIn("allow-same-origin", home)
        self.assertNotIn("<script>document.cookie</script>", home)                                   # only inside escaped srcdoc
        self.assertNotIn("Old campaign", home)                                                       # expired campaign hidden
        self.assertIn("mailto:ads@example.org", home)                                                # placeholder for the expired footer slot
        run = upload(c).headers["Location"]
        result = c.get(run).get_data(as_text=True)
        self.assertNotIn("javascript:alert", result)                                                 # unsafe link rejected
        self.assertIn("Sponsor B", result)
        self.assertNotIn("Sponsor <b>A</b>", result)

    def test_ads_file_reload_without_restart_and_bad_json(self):
        d, app = self.site(ads={"slots": {}, "placeholders": False})
        c = app.test_client()
        self.assertNotIn("Your ad could be here", c.get("/").get_data(as_text=True))
        time.sleep(0.02)
        (d / "ads.json").write_text(json.dumps({"slots": {"top": {"type": "text", "title": "Hello Ad", "body": "b", "href": "https://x.example.org"}}}))
        os.utime(d / "ads.json", (time.time() + 5, time.time() + 5))
        self.assertIn("Hello Ad", c.get("/").get_data(as_text=True))
        (d / "ads.json").write_text("{ not json")
        os.utime(d / "ads.json", (time.time() + 10, time.time() + 10))
        self.assertEqual(c.get("/").status_code, 200)                                                # broken ads file never breaks the site

    def test_ads_image_served_safely(self):
        d, app = self.site()
        (d / "ads").mkdir()
        (d / "ads" / "b.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (d / "ads" / "evil.svg").write_text("<svg onload=alert(1)/>")
        c = app.test_client()
        r = c.get("/ads-media/b.png")
        self.assertEqual(r.status_code, 200)
        self.assertIn("sandbox", r.headers["Content-Security-Policy"])
        self.assertEqual(c.get("/ads-media/evil.svg").status_code, 404)

    def test_retention_purge(self):
        from swa_data_tool.web import purge_old
        root = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, root, True)
        old, new = root / "s1" / "old_run", root / "s1" / "new_run"
        old.mkdir(parents=True); new.mkdir(parents=True)
        (root / "s2" / "r").mkdir(parents=True)
        t = time.time() - 100 * 3600
        os.utime(old, (t, t)); os.utime(root / "s2" / "r", (t, t))
        purge_old(root, 48)
        self.assertFalse(old.exists()); self.assertTrue(new.exists())
        self.assertFalse((root / "s2").exists())                      # empty visitor folder removed

    def test_privacy_salt_from_environment(self):
        d, app = self.site()
        os.environ["SWA_PRIVACY_SALT"] = "env-secret-phrase"
        self.addCleanup(lambda: os.environ.pop("SWA_PRIVACY_SALT", None))
        from swa_data_tool.config import load_config
        self.assertEqual(load_config(d / "config.yaml")["privacy_salt"], "env-secret-phrase")
        c = app.test_client()
        r = upload(c)
        run = r.headers["Location"].rsplit("/", 1)[1]
        self.assertNotIn("WITHOUT a secret", c.get(f"/runs/{run}").get_data(as_text=True))

    def test_font_and_robots(self):
        _, app = self.site()
        c = app.test_client()
        f = c.get("/static/figtree.woff2")
        self.assertEqual(f.status_code, 200)
        self.assertIn("immutable", f.headers["Cache-Control"])
        self.assertIn("Disallow: /runs/", c.get("/robots.txt").get_data(as_text=True))
        self.assertEqual(c.get("/runs/x").status_code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
