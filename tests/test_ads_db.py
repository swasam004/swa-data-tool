import io, json, shutil, tempfile, unittest
from pathlib import Path

from PIL import Image

from swa_data_tool.ads import Ads, process_image_upload
from swa_data_tool.ads_db import attach


class FakeStore:
    def __init__(self, fail=False):
        self.raw, self.fail, self.puts = None, fail, 0

    def get(self):
        if self.fail:
            raise OSError("db down")
        return self.raw

    def put(self, raw):
        if self.fail:
            raise OSError("db down")
        self.raw, self.puts = raw, self.puts + 1


def fresh():
    d = Path(tempfile.mkdtemp())
    return d, Ads(d / "ads.json"), d / "ads"


class AdsDbTests(unittest.TestCase):
    def test_ads_and_images_survive_a_wiped_disk(self):
        store = FakeStore()
        d1, ads1, img1 = fresh(); self.addCleanup(shutil.rmtree, d1, True)
        attach(ads1, img1, store)
        buf = io.BytesIO(); Image.new("RGB", (400, 60), "blue").save(buf, "PNG")
        url, msg, _ = process_image_upload(buf.getvalue(), img1)
        d = ads1.ensure_ids()
        d["slots"]["top"].append({"type": "image", "src": url, "href": "https://a.example.org", "alt": "A", "id": "x1"})
        ads1.save(d)
        self.assertGreaterEqual(store.puts, 1)
        # simulate Render restart: brand-new empty disk, same database
        d2, ads2, img2 = fresh(); self.addCleanup(shutil.rmtree, d2, True)
        attach(ads2, img2, store)
        self.assertEqual(ads2.sync_error, "")
        self.assertEqual(ads2.raw()["slots"]["top"][0]["id"], "x1")
        self.assertTrue((img2 / Path(url).name).is_file())
        self.assertIn("ads-media", ads2.render("top"))

    def test_seeds_database_from_repo_ads_json(self):
        store = FakeStore()
        d, ads, img = fresh(); self.addCleanup(shutil.rmtree, d, True)
        (d / "ads.json").write_text(json.dumps({"slots": {"top": {"type": "text", "title": "Seed", "href": "https://s.example.org"}}}))
        attach(ads, img, store)
        self.assertIsNotNone(store.raw)

    def test_unreachable_database_never_overwrites_and_reports(self):
        import swa_data_tool.ads_db as m
        m.time.sleep = lambda s: None
        store = FakeStore(fail=True)
        d, ads, img = fresh(); self.addCleanup(shutil.rmtree, d, True)
        attach(ads, img, store)
        self.assertIn("Could not reach", ads.sync_error)
        self.assertIsNone(ads.on_save)

    def test_failed_push_keeps_local_save_and_reports(self):
        store = FakeStore()
        d, ads, img = fresh(); self.addCleanup(shutil.rmtree, d, True)
        attach(ads, img, store)
        store.fail = True
        dd = ads.ensure_ids(); dd["contact"] = "mailto:a@b.co"; ads.save(dd)
        self.assertIn("database copy failed", ads.sync_error)
        self.assertEqual(ads.contact, "mailto:a@b.co")


if __name__ == "__main__":
    unittest.main()
