# SWA Data Analysis - website guide

## 1. Update your live site (swa-data-tool.onrender.com)
1. In your project/repo, replace these with the versions in this zip: `swa_data_tool/` (whole folder), `pyproject.toml`,
   `requirements.txt`, `render.yaml`. Add the new `ads.json`, `ads/` and `profiles/`.
   **Keep your own `config.yaml`** (or use the one included).
2. Commit and push. Render rebuilds automatically.
3. In Render → your service → **Environment**, add:
   | Variable | Value |
   |---|---|
   | `SWA_PRIVACY_SALT` | a long secret phrase you invent (used to hash personal data; keeps it out of git) |
   | `SWA_ADMIN_PASSWORD` | a strong password – the only way to open **/settings** on the public site |
   | `SWA_SECRET` | any long random text (Render generates it if you use `render.yaml`) |
   | `SWA_RETENTION_HOURS` | `48` (results auto-delete after this time) |
4. Open `https://swa-data-tool.onrender.com/settings` – the browser asks for a password; user name can be anything,
   password = `SWA_ADMIN_PASSWORD`.

## 2. What changed (and why)
* **New design**: data-analysis colours, Canva-style hero with a standalone drop card, self-hosted *Figtree* font.
* **No fixed title**: the page no longer shows "Youth Programme - Beneficiary Register". Each upload has an optional
  **Name this analysis** box; if left empty the file name is used. That name appears in the page and in the Excel/HTML report.
* **swasamson.site** is advertised in the header button, footer, a promo section and a small line in every report
  (turn the report line off with `branding: false` in config.yaml).
* **Advertising slots** (section 3).
* **Visitor privacy (important for a public site)**: before, everybody saw everybody's past runs, and anyone could edit
  Settings. Now each browser only sees its own results (404 for anything else), Settings need the admin password,
  results are deleted after `SWA_RETENTION_HOURS`, and the uploaded file is deleted right after analysis.
* **Type of data** drop-down appears automatically when you put extra `.yaml` files in `profiles/` (the sales example is included).

## 3. Selling / placing ads
Edit `ads.json` (no restart needed). Four slots:

| Slot | Where | Size |
|---|---|---|
| `top` | under the header, home page | 728 × 90 |
| `home_below` | under the upload box | 970 × 250 |
| `result_below` | under a finished report | 728 × 120 |
| `footer` | bottom of every page | 728 × 90 |

Each slot holds **one ad or a list** (one is picked at random per page view). Ad types:
* `image` – banner (`/ads-media/file.png` from the `ads/` folder, or an https image URL) + link.
* `text` – title, body, link, button text.
* `html` – script/embed code from an ad network. It runs in a **sandboxed frame**: it cannot read your visitors' uploads,
  reports or cookies. Because of that, some networks (e.g. Google AdSense) may refuse to show ads there; direct sponsors,
  affiliate banners and most embeds work. If you want AdSense, put it on swasamson.site rather than on the page that
  handles beneficiary data.
* Add `"start"` / `"end"` dates (YYYY-MM-DD) to run a paid campaign automatically. Expired ads disappear.
* Empty slots show a dashed **"Your ad could be here – Advertise with us"** box linking to `contact`
  (set `"contact": "mailto:you@example.com"`). Set `"placeholders": false` to hide empty slots.
* Every ad is labelled "Advertisement", and links use `rel="sponsored nofollow noopener"`.
See `ads.example.json` for a complete example.

Good practice: tell advertisers that visitors are NGOs/researchers; do not accept ads that track or collect data from
people who are uploading sensitive files.

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
