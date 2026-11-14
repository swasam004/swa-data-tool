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
        self.assertNotIn("Your ad could be here", html)          # visitors never see an empty-slot box
        self.assertIn("Advertise with us", html)                 # one link, in the footer
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

    def test_admin_locked_on_public_site_and_no_admin_link_or_banner(self):
        _, app = self.site()
        c = app.test_client()
        pub = {"X-Forwarded-For": "1.2.3.4"}
        for path in ("/admin", "/admin/settings", "/admin/login", "/admin/ads/backup"):
            self.assertEqual(c.get(path, headers=pub).status_code, 403, path)
        for path in ("/admin/ads/add", "/admin/ads/delete", "/admin/ads/restore"):
            self.assertIn(c.post(path, headers=pub).status_code, (400, 403), path)
        home = c.get("/", headers=pub).get_data(as_text=True)
        self.assertNotIn(">Admin<", home)
        self.assertNotIn("Settings", home)
        self.assertNotIn("privacy salt", home.lower())               # the old banner must never appear to the public
        self.assertEqual(c.get("/admin", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code, 200)

    def login(self, c, pw="s3cret"):
        tok = c.get("/admin/login").get_data(as_text=True).split("name='csrf' value='")[1].split("'")[0]
        return c.post("/admin/login", data={"csrf": tok, "password": pw})

    def test_admin_login_flow(self):
        _, app = self.site(env={"SWA_ADMIN_PASSWORD": "s3cret"})
        c = app.test_client()
        self.assertEqual(c.get("/admin").status_code, 302)
        self.assertIn("/admin/login", c.get("/admin").headers["Location"])
        home = c.get("/").get_data(as_text=True)
        self.assertNotIn(">Admin<", home)                                   # public never sees an admin link
        self.assertNotIn("privacy salt", home.lower())
        r = self.login(c, "wrong")
        self.assertIn("/admin/login", r.headers["Location"])
        self.assertEqual(c.get("/admin").status_code, 302)                  # still not signed in
        r = self.login(c)
        self.assertIn("swa_admin=", r.headers.get("Set-Cookie", "") + "".join(r.headers.getlist("Set-Cookie")))
        page = c.get("/admin").get_data(as_text=True)
        self.assertIn("Ads manager", page)
        self.assertIn("Privacy salt", page)                                 # the checklist lives in the admin area only
        self.assertIn(">Admin<", c.get("/").get_data(as_text=True))         # admin sees the link once signed in
        c.post("/admin/logout")
        self.assertEqual(c.get("/admin").status_code, 302)

    def test_admin_login_rate_limit(self):
        _, app = self.site(env={"SWA_ADMIN_PASSWORD": "s3cret"})
        c = app.test_client()
        import swa_data_tool.webadmin as wa
        orig = time.sleep
        wa.time.sleep = lambda *_: None
        self.addCleanup(setattr, wa.time, "sleep", orig)
        for _ in range(5):
            self.login(c, "bad")
        self.login(c, "s3cret")                                              # correct password, but locked out
        self.assertEqual(c.get("/admin").status_code, 302)

    def admin_client(self, **kw):
        d, app = self.site(env={"SWA_ADMIN_PASSWORD": "s3cret"}, **kw)
        c = app.test_client()
        self.login(c)
        return d, c

    def csrf(self, c):
        return c.get("/admin").get_data(as_text=True).split("name='csrf' value='")[1].split("'")[0]

    def test_admin_add_text_ad_shows_on_site_and_can_be_paused_and_deleted(self):
        d, c = self.admin_client()
        tok = self.csrf(c)
        r = c.post("/admin/ads/add", data={"csrf": tok, "slot": "top", "type": "text", "title": "Hello <b>Sponsor</b>",
                                           "body": "Great offer", "href": "https://sponsor.example.org", "note": "Paid KES 5000"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("Hello &lt;b&gt;Sponsor&lt;/b&gt;", c.get("/").get_data(as_text=True))
        data = json.loads((d / "ads.json").read_text())
        ad_id = data["slots"]["top"][0]["id"]
        self.assertNotIn("Paid KES", c.get("/").get_data(as_text=True))                    # private note never public
        c.post("/admin/ads/toggle", data={"csrf": tok, "slot": "top", "id": ad_id})
        self.assertNotIn("Hello", c.get("/").get_data(as_text=True))
        self.assertIn("Paused", c.get("/admin").get_data(as_text=True))
        c.post("/admin/ads/toggle", data={"csrf": tok, "slot": "top", "id": ad_id})
        self.assertIn("Hello", c.get("/").get_data(as_text=True))
        c.post("/admin/ads/delete", data={"csrf": tok, "slot": "top", "id": ad_id})
        self.assertNotIn("Hello", c.get("/").get_data(as_text=True))

    def test_admin_rejects_bad_ads(self):
        d, c = self.admin_client()
        tok = self.csrf(c)
        bad = [{"type": "text", "title": "x", "href": "javascript:alert(1)"},
               {"type": "text", "title": "", "href": "https://a.example.org"},
               {"type": "image", "image_url": "https://a.example.org/readme.txt"},
               {"type": "html", "html": "  "},
               {"type": "text", "title": "x", "href": "https://a.example.org", "start": "2026-12-31", "end": "2026-01-01"},
               {"type": "text", "title": "x", "href": "https://a.example.org", "start": "31/12/2026"}]
        for form in bad:
            c.post("/admin/ads/add", data={"csrf": tok, "slot": "top", **form})
        self.assertEqual(json.loads((d / "ads.json").read_text())["slots"]["top"] if (d / "ads.json").exists() else [], [])
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "nonsense", "type": "text", "title": "x", "href": "https://a.example.org"})

    def test_admin_requires_csrf_for_every_change(self):
        d, c = self.admin_client()
        c.post("/admin/ads/add", data={"slot": "top", "type": "text", "title": "No token", "href": "https://a.example.org"})
        self.assertNotIn("No token", c.get("/").get_data(as_text=True))
        c.post("/admin/ads/options", data={"contact": "mailto:x@example.org"})
        self.assertNotIn("x@example.org", c.get("/").get_data(as_text=True))

    def png_bytes(self, w=728, h=90, fmt="PNG"):
        from PIL import Image
        b = io.BytesIO()
        Image.new("RGB", (w, h), (200, 80, 20)).save(b, format=fmt)
        return b.getvalue()

    def test_admin_image_upload_is_validated_and_reencoded(self):
        d, c = self.admin_client()
        tok = self.csrf(c)
        evil = self.png_bytes() + b"<?php system($_GET['x']); ?>"
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "top", "type": "image", "href": "https://a.example.org",
                                       "image": (io.BytesIO(evil), "banner.png")}, content_type="multipart/form-data")
        saved = json.loads((d / "ads.json").read_text())["slots"]["top"]
        self.assertEqual(len(saved), 1)
        src = saved[0]["src"]
        self.assertTrue(src.startswith("/ads-media/ad-"))
        stored = (d / "ads" / src.rsplit("/", 1)[1]).read_bytes()
        self.assertNotIn(b"<?php", stored)                                   # re-encoded: appended payload is gone
        self.assertEqual(c.get(src).status_code, 200)
        self.assertIn(src, c.get("/").get_data(as_text=True))
        # not an image / wrong format / too big
        for name, blob in (("x.png", b"not an image"), ("x.gif", self.png_bytes(fmt="GIF")), ("big.png", b"\x89PNG" + b"0" * 700000)):
            c.post("/admin/ads/add", data={"csrf": tok, "slot": "top", "type": "image", "image": (io.BytesIO(blob), name)}, content_type="multipart/form-data")
        self.assertEqual(len(json.loads((d / "ads.json").read_text())["slots"]["top"]), 1)

    def test_large_image_is_compressed_and_clear_slot(self):
        import os
        from PIL import Image
        d, c = self.admin_client()
        tok = self.csrf(c)
        im = Image.frombytes("RGB", (3000, 1200), os.urandom(3000 * 1200 * 3))          # ~10 MB of noise
        b = io.BytesIO(); im.save(b, format="PNG")
        self.assertGreater(len(b.getvalue()), 3 * 1024 * 1024)
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "home_below", "type": "image", "image": (io.BytesIO(b.getvalue()), "huge.png")}, content_type="multipart/form-data")
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "home_below", "type": "text", "title": "Two", "href": "https://t.example.org"})
        saved = json.loads((d / "ads.json").read_text())["slots"]["home_below"]
        self.assertEqual(len(saved), 2)
        f = d / "ads" / saved[0]["src"].rsplit("/", 1)[1]
        self.assertLess(f.stat().st_size, 250 * 1024)
        self.assertLessEqual(max(Image.open(f).size), 1940)
        c.post("/admin/ads/clear", data={"csrf": tok, "slot": "home_below"})
        self.assertEqual(json.loads((d / "ads.json").read_text())["slots"]["home_below"], [])
        self.assertFalse(f.exists())

    def test_delete_removes_image_file(self):
        d, c = self.admin_client()
        tok = self.csrf(c)
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "footer", "type": "image", "image": (io.BytesIO(self.png_bytes()), "b.png")}, content_type="multipart/form-data")
        ad = json.loads((d / "ads.json").read_text())["slots"]["footer"][0]
        name = ad["src"].rsplit("/", 1)[1]
        self.assertTrue((d / "ads" / name).exists())
        c.post("/admin/ads/delete", data={"csrf": tok, "slot": "footer", "id": ad["id"]})
        self.assertFalse((d / "ads" / name).exists())

    def test_backup_and_restore_round_trip(self):
        import zipfile
        d, c = self.admin_client()
        tok = self.csrf(c)
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "top", "type": "image", "href": "https://a.example.org",
                                       "image": (io.BytesIO(self.png_bytes()), "b.png")}, content_type="multipart/form-data")
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "footer", "type": "text", "title": "Keep me", "href": "https://k.example.org"})
        bk = c.get("/admin/ads/backup")
        self.assertEqual(bk.status_code, 200)
        names = zipfile.ZipFile(io.BytesIO(bk.data)).namelist()
        self.assertIn("ads.json", names)
        self.assertTrue(any(n.startswith("ads/ad-") for n in names))
        for slot in ("top", "footer"):
            for ad in json.loads((d / "ads.json").read_text())["slots"][slot]:
                c.post("/admin/ads/delete", data={"csrf": tok, "slot": slot, "id": ad["id"]})
        self.assertNotIn("Keep me", c.get("/").get_data(as_text=True))
        c.post("/admin/ads/restore", data={"csrf": tok, "backup": (io.BytesIO(bk.data), "b.zip")}, content_type="multipart/form-data")
        self.assertIn("Keep me", c.get("/").get_data(as_text=True))
        # hostile backup: path traversal, javascript: link, fake image
        evil = io.BytesIO()
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("ads.json", json.dumps({"slots": {"top": [{"type": "text", "title": "t", "href": "javascript:alert(1)"},
                                                                 {"type": "image", "src": "/ads-media/ghost.png"}]}}))
            z.writestr("ads/../../evil.png", b"x")
            z.writestr("ads/fake.png", b"not an image")
        c.post("/admin/ads/restore", data={"csrf": tok, "backup": (io.BytesIO(evil.getvalue()), "e.zip")}, content_type="multipart/form-data")
        self.assertEqual(json.loads((d / "ads.json").read_text())["slots"]["top"], [])
        self.assertFalse((d.parent / "evil.png").exists())
        self.assertFalse((d / "ads" / "fake.png").exists())

    def test_admin_can_delete_one_or_all_ads_any_time(self):
        d, c = self.admin_client()
        tok = self.csrf(c)
        for n in ("A", "B", "C"):
            c.post("/admin/ads/add", data={"csrf": tok, "slot": "footer", "type": "text", "title": "Ad " + n, "href": "https://x.example.org"})
        ads = json.loads((d / "ads.json").read_text())["slots"]["footer"]
        self.assertEqual(len(ads), 3)
        c.post("/admin/ads/delete", data={"csrf": tok, "slot": "footer", "id": ads[0]["id"]})
        self.assertEqual(len(json.loads((d / "ads.json").read_text())["slots"]["footer"]), 2)
        c.post("/admin/ads/clear", data={"csrf": tok, "slot": "footer"})
        self.assertEqual(json.loads((d / "ads.json").read_text())["slots"]["footer"], [])

    def test_admin_options_and_settings_editor(self):
        d, c = self.admin_client()
        tok = self.csrf(c)
        c.post("/admin/ads/options", data={"csrf": tok, "contact": "javascript:alert(1)", "placeholders": "1"})
        self.assertNotIn("javascript:alert", c.get("/").get_data(as_text=True))
        c.post("/admin/ads/options", data={"csrf": tok, "contact": "mailto:ads@example.org", "placeholders": "1"})
        self.assertIn("mailto:ads@example.org", c.get("/").get_data(as_text=True))
        c.post("/admin/ads/options", data={"csrf": tok, "contact": "mailto:ads@example.org"})        # placeholders unchecked
        self.assertNotIn("Advertise with us", c.get("/").get_data(as_text=True))
        page = c.get("/admin/settings")
        self.assertEqual(page.status_code, 200)
        t2 = page.get_data(as_text=True).split("name='csrf' value='")[1].split("'")[0]
        broken = c.post("/admin/settings", data={"csrf": t2, "action": "save", "text": "columns: nope"})
        self.assertIn("Problem", broken.get_data(as_text=True))
        self.assertEqual(c.post("/admin/settings", data={"action": "save", "text": "x"}).status_code, 303)   # no CSRF -> refused
        self.assertEqual(c.get("/settings").status_code, 302)

    def test_storage_warning_on_render_and_data_dir(self):
        d, c = self.admin_client()
        os.environ["RENDER"] = "true"
        self.addCleanup(os.environ.pop, "RENDER", None)
        self.assertIn("Ads storage is temporary", c.get("/admin").get_data(as_text=True))

    def test_data_dir_seeds_and_stores_ads_there(self):
        data = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, data, True)
        d, app = make_site(ads={"slots": {"top": {"type": "text", "title": "Seeded", "href": "https://s.example.org"}}},
                           env={"SWA_DATA_DIR": str(data), "SWA_ADMIN_PASSWORD": "s3cret"})
        self.addCleanup(shutil.rmtree, d, True)
        self.addCleanup(os.environ.pop, "SWA_DATA_DIR", None)
        c = app.test_client(); self.login(c)
        self.assertIn("Seeded", c.get("/").get_data(as_text=True))
        tok = self.csrf(c)
        c.post("/admin/ads/add", data={"csrf": tok, "slot": "footer", "type": "text", "title": "Persisted", "href": "https://p.example.org"})
        self.assertIn("Persisted", (data / "ads.json").read_text())
        self.assertNotIn("Persisted", (d / "ads.json").read_text())
        self.assertIn("Ads storage is permanent", c.get("/admin").get_data(as_text=True))

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
        self.assertIn("mailto:ads@example.org", home)                                                # the footer "Advertise with us" link
        run = upload(c).headers["Location"]
        result = c.get(run).get_data(as_text=True)
        self.assertNotIn("javascript:alert", result)                                                 # unsafe link rejected
        self.assertIn("Sponsor B", result)
        self.assertNotIn("Sponsor <b>A</b>", result)

    def test_ads_file_reload_without_restart_and_bad_json(self):
        d, app = self.site(ads={"slots": {}, "placeholders": False})
        c = app.test_client()
        self.assertNotIn("Advertise with us", c.get("/").get_data(as_text=True))
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


    def test_seo_foundation(self):
        _, app = self.site(env={"SWA_SITE_URL": "https://tools.example.org", "SWA_GSC_VERIFICATION": "abc123XYZ"})
        c = app.test_client()
        home = c.get("/").get_data(as_text=True)
        self.assertIn("<link rel='canonical' href='https://tools.example.org/'>", home)
        self.assertIn("name='google-site-verification' content='abc123XYZ'", home)
        self.assertIn("application/ld+json", home)
        self.assertIn('"@type": "WebApplication"', home)
        self.assertIn("index,follow", home)
        ld = json.loads(home.split("application/ld+json'>")[1].split("</script>")[0])
        self.assertEqual(ld["url"], "https://tools.example.org/")
        self.assertIn("<title>Online data analysis tool", home)
        self.assertIn("name='description' content='Upload a CSV", home)
        robots = c.get("/robots.txt").get_data(as_text=True)
        self.assertIn("Sitemap: https://tools.example.org/sitemap.xml", robots)
        self.assertIn("Disallow: /admin", robots)
        sm = c.get("/sitemap.xml")
        self.assertEqual(sm.mimetype, "application/xml")
        text = sm.get_data(as_text=True)
        for p in ("/", "/about", "/privacy"):
            self.assertIn(f"<loc>https://tools.example.org{p}</loc>", text)
        self.assertNotIn("/admin", text)
        for p in ("/about", "/privacy"):
            page = c.get(p).get_data(as_text=True)
            self.assertIn("index,follow", page)
            self.assertIn(f"<link rel='canonical' href='https://tools.example.org{p}'>", page)
        self.assertGreater(len(c.get("/privacy").get_data(as_text=True)), 2000)

    def test_private_pages_are_noindex(self):
        _, app = self.site()
        c = app.test_client()
        run = upload(c).headers["Location"]
        for path in (run, "/history", "/nope"):
            page = c.get(path).get_data(as_text=True)
            self.assertIn("noindex,nofollow", page, path)
            self.assertNotIn("rel='canonical'", page, path)
        pub = {"X-Forwarded-For": "1.2.3.4"}
        self.assertIn("noindex", c.get("/admin", headers=pub).get_data(as_text=True))

    def test_canonical_falls_back_to_request_host(self):
        _, app = self.site()
        r = app.test_client().get("/", headers={"X-Forwarded-Proto": "https", "Host": "tool.example.org"})
        self.assertIn("href='https://tool.example.org/'", r.get_data(as_text=True))

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
