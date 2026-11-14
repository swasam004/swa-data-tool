# SWA Data Analysis - website guide

## 1. Update your live site (swa-data-tool.onrender.com)
1. Replace `swa_data_tool/` (whole folder), `pyproject.toml`, `requirements.txt` and `render.yaml` with the versions in this
   zip, and add `WEB_GUIDE.md`, `ads.json`, `ads/` and `profiles/` if you don't have them. Keep your own `config.yaml`.
2. `git add -A && git commit -m "Admin panel" && git push` - Render rebuilds automatically.
3. In Render → your service → **Environment**, add:
   | Variable | Value |
   |---|---|
   | `SWA_ADMIN_PASSWORD` | a strong password - **required to use the admin panel** |
   | `SWA_PRIVACY_SALT` | a long secret phrase (hashing of personal data; keeps it out of git) |
   | `SWA_SECRET` | any long random text (keeps admin/forms valid across restarts) |
   | `SWA_RETENTION_HOURS` | `48` |
4. Open `https://swa-data-tool.onrender.com/admin`, sign in with the admin password.

## 2. The admin panel (`/admin`)
Only you can open it. There is **no link to it on the public site** (it appears in the menu only after you sign in).
* **Setup checklist** - privacy salt, admin password, storage, retention, advertiser contact. Warnings about setup live
  here, never on the public pages.
* **Ads manager** - four slots. For each: *Add an ad* (image upload, text ad, or embed code), choose start/end dates,
  *Pause/Resume*, *Delete*. Changes are live immediately. A private note (advertiser, price) is saved but never shown.
  Uploaded images are verified and re-encoded for safety (PNG/JPG/WEBP, up to 600 KB).
* **Advertiser contact** - the e-mail or link behind "Advertise with us", and whether empty slots show that box.
* **Backup & restore** - download all ads (with images) as one .zip, restore it later.
* **Analysis settings** - edit config.yaml (the columns and pivots the site expects) with a Check button.
* Sign-in is locked for 10 minutes after 5 wrong passwords. Sessions last 8 hours.

### Important: where ads are saved
Render's free plan has a **temporary disk**: ads added in the panel disappear when the service restarts or redeploys.
Choose one:
* **Back up after every change** (Backup & restore) and restore when needed; or commit the downloaded `ads.json` and
  `ads/` images into the repo (they become the starting set on every deploy); or
* **Attach a Render persistent disk** (paid), mount it at `/var/data`, and set `SWA_DATA_DIR=/var/data`. Ads then
  survive everything. On first start the repo's `ads.json` is copied there as the starting set.
The admin checklist tells you which situation you are in.

## 3. Ads - what visitors see
Slots: `top` (728×90, home page), `home_below` (970×250, under the upload box), `result_below` (728×120, under a report),
`footer` (728×90, every page). A slot with several live ads shows one at random per page view. Empty slots show
"Your ad could be here - Advertise with us" (turn off in the admin panel). Every ad is labelled "Advertisement" and links
use `rel="sponsored nofollow noopener"`.
Embed/script ads run in a **sandboxed frame**: they cannot read visitors' uploads, reports or cookies. Because of that some
networks (e.g. AdSense) may refuse to show ads there; direct sponsors, image/text ads and most embeds work.
You can still edit `ads.json` by hand (see `ads.example.json`), but the panel is easier.

## 4. Run it on your own computer
```
pip install .
swa-tool serve --config config.yaml        # http://127.0.0.1:8080  (Settings open freely from the same computer)
```

## 5. Limits
* One analysis runs at a time; a second visitor waits a few seconds.
* Render's free plan sleeps when idle (first visit is slow) and its disk is temporary – results vanish on redeploy.
* The free plan has ~512 MB RAM: very large files (tens of MB) may fail. Lower `SWA_MAX_MB` if needed.
* The page expects the columns described by `config.yaml` (or the chosen profile). A file with other headers is
  rejected with a clear message. Automatic column detection for arbitrary files is not part of this version.
