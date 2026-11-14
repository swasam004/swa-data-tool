# Getting SWA Data Analysis found on Google

Honest expectations: Google decides rankings; nobody can guarantee a position. Canva-level visibility takes months of
steady work. The site now has the technical foundation; the steps below are what only you can do.

## A. Give the site a proper address (most important)
`swa-data-tool.onrender.com` is a shared Render address and ranks poorly. Use your own domain, for example
`tools.swasamson.site` (a subdomain of the site you already own):
1. Render → your service → **Settings → Custom Domains → Add Custom Domain** → `tools.swasamson.site`.
2. At your domain registrar / DNS, add the **CNAME** record Render shows (name `tools`, value `swa-data-tool.onrender.com`).
3. Wait until Render shows the domain as verified (HTTPS certificate is issued automatically).
4. Render → **Environment** → add `SWA_SITE_URL` = `https://tools.swasamson.site` (used for canonical links and the sitemap).

## B. Tell Google the site exists (Google Search Console)
1. Go to search.google.com/search-console → **Add property** → *URL prefix* → `https://tools.swasamson.site`.
2. Choose **HTML tag** verification. Copy only the long code from `content="..."`.
3. Render → Environment → add `SWA_GSC_VERIFICATION` = that code → save (the service restarts) → click **Verify** in Search Console.
4. Search Console → **Sitemaps** → submit `sitemap.xml`.
5. Search Console → **URL inspection** → paste the home page address → **Request indexing**.
Indexing usually takes days to a few weeks.

## C. Keep the site awake
On Render's free plan the service sleeps when idle; the first visit (and Google's crawler) can time out. Use the paid
"Starter" plan, or a free uptime monitor (e.g. UptimeRobot) pinging `https://tools.swasamson.site/healthz` every 5 minutes.

## D. Build trust and links (this is what moves rankings)
* Link to the tool from **swasamson.site** (menu, homepage, a blog post) - the links already carry tracking tags.
* Write helpful pages on swasamson.site that target what people search for, each linking to the tool, e.g.
  "How to analyse KoboToolbox data in Excel", "Pivot table from a CSV: step by step", "How to disaggregate indicators by sex and age group".
* Share it in places NGO/M&E people gather: LinkedIn, KoboToolbox community forum, M&E and data-for-development groups,
  university and NGO networks. Real links from other sites matter far more than anything on the page itself.
* Use the same name ("SWA Data Analysis") and a short description everywhere (LinkedIn, GitHub, social profiles).

## E. Already done on the site
Canonical links, a sitemap (`/sitemap.xml`), `robots.txt` that hides results/admin pages, structured data (WebApplication),
keyword-rich title and description, fast self-hosted font, mobile-friendly layout, HTTPS (from Render), About and Privacy
pages. Private pages (results, history, admin) are marked `noindex` so they can never appear in Google.

## F. Measure it
Search Console → **Performance** shows what people typed to find you. Add content around the phrases that already show up.
