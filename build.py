#!/usr/bin/env python3
"""
Build a static software-alternatives directory and optionally submit its URLs
to IndexNow. Uses only the Python standard library.

CSV HTML trust boundary:
- title, descriptions, headings, FAQ fields, and categories are escaped.
- data_table_html and widget_html are intentionally inserted as HTML fragments.
  Only put trusted, reviewed HTML in those two CSV columns.
"""

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

# ---------------------------------------------------------------------------
# HARDCODED CONFIGURATION CONSTANTS (intentionally kept unchanged)
# ---------------------------------------------------------------------------
DOMAIN = "software-alternatives-atlas.netlify.app"
BASE_URL = "https://software-alternatives-atlas.netlify.app"
INDEXNOW_KEY = "b223b6b305c1482abbf0ed50c7ff86f7"
CSV_FILE = "data.csv"
OUTPUT_DIR = "dist"
STATE_FILE = "submitted_urls.json"
DRIP_BATCH_SIZE = 250

EXPECTED_HEADERS = [
    "slug", "title", "meta_description", "h1", "category", "summary",
    "data_table_html", "widget_html", "faq_q1", "faq_a1", "faq_q2", "faq_a2",
]
SITEMAP_LIMIT = 5000
KEY_FILE = INDEXNOW_KEY + ".txt"


def write_netlify_config(root):
    content = (
        "[build]\n"
        '  command = "python3 build.py"\n'
        '  publish = "dist"\n'
    )
    (root / "netlify.toml").write_text(content, encoding="utf-8")


def clean_slug(value):
    """Return a URL-safe, lowercase, hyphenated slug."""
    value = (value or "").strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    # Restrict path components to portable ASCII URL characters.
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def read_rows(csv_path):
    if not csv_path.is_file():
        raise FileNotFoundError(
            f"Required CSV not found: {csv_path}. Place data.csv beside build.py."
        )
    rows = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != EXPECTED_HEADERS:
            raise ValueError(
                "CSV headers must match exactly, in this order:\n"
                + ",".join(EXPECTED_HEADERS)
            )
        seen = set()
        for line_number, raw in enumerate(reader, start=2):
            row = {key: (raw.get(key) or "").strip() for key in EXPECTED_HEADERS}
            slug = clean_slug(row["slug"])
            if not slug:
                raise ValueError(f"Row {line_number}: slug is empty after cleaning.")
            if slug in seen:
                raise ValueError(f"Row {line_number}: duplicate slug after cleaning: {slug}")
            seen.add(slug)
            row["slug"] = slug
            if not row["title"]:
                row["title"] = slug.replace("-", " ").title()
            if not row["h1"]:
                row["h1"] = row["title"]
            if not row["summary"]:
                row["summary"] = row["meta_description"] or row["title"]
            if not row["category"]:
                row["category"] = "Software"
            rows.append(row)
    return rows


def esc(value):
    return html.escape(str(value or ""), quote=True)


def json_script(data):
    # Prevent a text value from terminating an HTML script element.
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def site_css():
    return r"""
:root{color-scheme:dark;--bg:#071321;--panel:#0d2032;--panel2:#10283a;--line:#1e3b4c;--text:#e9f2f5;--muted:#9bb1bc;--green:#34d399;--green2:#10b981;--white:#fff}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(ellipse at 15% 0%,#123047 0,transparent 42%),var(--bg);color:var(--text);font:16px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
a{color:#6ee7b7;text-decoration:none}a:hover{text-decoration:underline;color:#a7f3d0}.wrap{width:min(1120px,calc(100% - 36px));margin:auto}
header{border-bottom:1px solid var(--line);background:#071321eF}.nav{min-height:68px;display:flex;align-items:center;justify-content:space-between;gap:20px}.brand{font-weight:850;letter-spacing:-.04em;color:var(--white);font-size:1.08rem}.brand span{color:var(--green)}
main{padding:44px 0 70px}.eyebrow{color:var(--green);font-size:.78rem;font-weight:800;text-transform:uppercase;letter-spacing:.14em}
h1{font-size:clamp(2rem,5vw,3.65rem);line-height:1.08;letter-spacing:-.045em;margin:.45rem 0 1rem;max-width:900px}h2{font-size:1.55rem;letter-spacing:-.025em;margin:2rem 0 .8rem}h3{line-height:1.25}.lead{color:#bed0d8;font-size:1.12rem;max-width:850px}
.panel,.ai-tldr,.faq,.guide-card{background:linear-gradient(145deg,var(--panel),#0a1a29);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 14px 40px #0002}
.ai-tldr{border-left:4px solid var(--green);margin:22px 0 28px}.ai-tldr strong{display:block;color:var(--green);font-size:.78rem;text-transform:uppercase;letter-spacing:.12em;margin-bottom:7px}
.fresh{display:inline-flex;gap:7px;align-items:center;border:1px solid #23634e;background:#0c3028;color:#a7f3d0;border-radius:99px;padding:5px 11px;font-size:.8rem;font-weight:700}
.ad-unit{min-height:90px;margin:24px 0;border:1px dashed #315065;border-radius:14px;background:#0a1a28;display:flex;align-items:center;justify-content:center;color:#66818f;font-size:.78rem}
.content-grid{display:grid;grid-template-columns:minmax(0,1fr) 280px;gap:24px;align-items:start}.side{position:sticky;top:20px}.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:14px;margin:18px 0}.table-wrap table{border-collapse:collapse;width:100%;min-width:520px}.table-wrap th,.table-wrap td{padding:12px 14px;text-align:left;border-bottom:1px solid var(--line)}.table-wrap th{background:#122b3d;color:#a7f3d0}
.btn,.pill{border:1px solid #285064;background:#10283a;color:#dcebf0;border-radius:999px;padding:8px 13px;cursor:pointer;font:inherit}.pill[aria-pressed=true],.btn.primary{background:#0c6b50;border-color:#15966c;color:white}.pills{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.search{width:100%;background:#091a28;border:1px solid #315064;border-radius:12px;color:white;padding:15px 16px;font:inherit;outline:none}.search:focus{border-color:var(--green);box-shadow:0 0 0 3px #34d39922}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:18px}.guide-card{padding:18px}.guide-card h3{margin:0 0 7px}.guide-card p{color:var(--muted);margin:0 0 12px}.muted,small{color:var(--muted)}.links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.links a{padding:12px 14px;background:#0c2030;border:1px solid var(--line);border-radius:12px}
footer{border-top:1px solid var(--line);padding:24px 0;color:var(--muted);font-size:.9rem}
@media(max-width:780px){.content-grid{grid-template-columns:1fr}.side{position:static}.cards{grid-template-columns:1fr}.links{grid-template-columns:1fr}main{padding-top:30px}.nav{min-height:60px}}
"""


def freshness_js():
    return """
<script>
(function(){
  var now=new Date();
  document.querySelectorAll('.live-year').forEach(function(el){el.textContent=now.getFullYear();});
  document.querySelectorAll('.live-updated').forEach(function(el){
    el.textContent=now.toLocaleDateString(undefined,{month:'long',year:'numeric'});
  });
})();
</script>
"""


def base_head(title, description, canonical, jsonld, og_title=None):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<meta name="robots" content="index,follow,max-image-preview:large">
<link rel="canonical" href="{esc(canonical)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Software Alternatives Atlas">
<meta property="og:title" content="{esc(og_title or title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical)}">
<meta name="twitter:card" content="summary">
<meta name="theme-color" content="#071321">
<style>{site_css()}</style>
<script type="application/ld+json">{json_script(jsonld)}</script>
</head>"""


def header():
    return """<header><div class="wrap nav">
<a class="brand" href="/">Software <span>Alternatives</span> Atlas</a>
<a href="/">Browse directory</a>
</div></header>"""


def footer(year):
    return f"""<footer><div class="wrap">
<p>© {year}–<span class="live-year">{year}</span> Software Alternatives Atlas. Independent software research directory.</p>
<p class="muted">Page information reviewed <span class="live-updated"></span>.</p>
</div></footer>{freshness_js()}"""


def make_page(row, rows, year):
    slug = row["slug"]
    canonical = f"{BASE_URL}/{quote(slug, safe='-')}/"
    title = f"{row['title']} Alternatives & Competitors ({year}) | Software Atlas"
    description = row["meta_description"] or row["summary"]
    summary = row["summary"]
    faqs = []
    for qkey, akey in (("faq_q1", "faq_a1"), ("faq_q2", "faq_a2")):
        if row[qkey] and row[akey]:
            faqs.append({"@type": "Question", "name": row[qkey],
                         "acceptedAnswer": {"@type": "Answer", "text": row[akey]}})
    faq_schema = {"@type": "FAQPage", "mainEntity": faqs}
    related = [r for r in rows if r["category"].casefold() == row["category"].casefold()
               and r["slug"] != slug][:6]
    item_list = {
        "@type": "ItemList", "name": f"Explore {row['category']} software alternatives",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "name": r["title"], "url": f"{BASE_URL}/{quote(r['slug'], safe='-')}/"}
            for i, r in enumerate(related)
        ],
    }
    app_schema = {
        "@type": "SoftwareApplication", "name": row["title"],
        "applicationCategory": row["category"],
        "description": summary, "url": canonical,
        "dateModified": dt.date.today().isoformat(),
    }
    graph = {"@context": "https://schema.org", "@graph": [faq_schema, item_list, app_schema]}
    faq_html = ""
    if faqs:
        faq_html = '<section><h2>Frequently asked questions</h2>'
        for faq in faqs:
            faq_html += (f'<div class="faq"><h3>{esc(faq["name"])}</h3>'
                         f'<p>{esc(faq["acceptedAnswer"]["text"])}</p></div>')
        faq_html += "</section>"
    related_html = ""
    if related:
        related_html = (
            f'<section><h2>Explore More {esc(row["category"])} Software Alternatives</h2>'
            '<div class="links">' +
            "".join(f'<a href="/{quote(r["slug"], safe="-")}/">{esc(r["title"])}</a>'
                    for r in related) + '</div></section>'
        )
    table_html = row["data_table_html"]
    widget_html = row["widget_html"]
    body = f"""{header()}
<main class="wrap">
<div class="eyebrow">{esc(row['category'])} comparison guide</div>
<h1>{esc(row['h1'])}</h1>
<div class="fresh">Updated for <span class="live-year">{year}</span> · Reviewed <span class="live-updated"></span></div>
<div class="ai-tldr"><strong>AI Quick Answer</strong><p>{esc(summary)}</p></div>
<div class="ad-unit" aria-label="Advertisement"></div>
<div class="content-grid"><article>
<p class="lead">{esc(description)}</p>
{f'<section><h2>Comparison overview</h2><div class="table-wrap">{table_html}</div></section>' if table_html else ''}
{f'<section><h2>Interactive comparison</h2>{widget_html}</section>' if widget_html else ''}
{related_html}
{faq_html}
</article>
<aside class="side"><div class="panel"><h2>At a glance</h2>
<p><strong>Category:</strong> {esc(row['category'])}</p>
<p><strong>Last reviewed:</strong> <span class="live-updated"></span></p>
<p class="muted">Use the directory to discover more tools and compare options.</p>
<a href="/">Search all software guides →</a></div>
<div class="ad-unit" aria-label="Advertisement"></div></aside>
</div></main>{footer(year)}"""
    return base_head(title, description, canonical, graph) + "<body>" + body + "</body></html>"


def make_homepage(rows, year):
    canonical = BASE_URL + "/"
    categories = sorted({r["category"] for r in rows}, key=str.casefold)
    search_data = [{
        "slug": r["slug"], "title": r["title"], "category": r["category"],
        "description": r["summary"],
        "url": f"/{quote(r['slug'], safe='-')}/",
    } for r in rows]
    homepage_schema = {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": f"Software Alternatives Atlas ({year})",
        "url": canonical,
        "dateModified": dt.date.today().isoformat(),
        "mainEntity": {"@type": "ItemList", "numberOfItems": len(rows),
                       "itemListElement": [
                           {"@type": "ListItem", "position": i + 1,
                            "url": item["url"], "name": item["title"]}
                           for i, item in enumerate(search_data[:100])
                       ]},
    }
    pills = '<button class="pill" data-category="*" aria-pressed="true">All categories</button>'
    pills += "".join(
        f'<button class="pill" data-category="{esc(cat)}" aria-pressed="false">{esc(cat)}</button>'
        for cat in categories
    )
    body = f"""{header()}
<main class="wrap">
<div class="eyebrow">Independent software discovery</div>
<h1>Find the right software. Compare smarter.</h1>
<p class="lead">Explore practical software alternatives, competitor roundups, and category-specific comparison guides.</p>
<div class="fresh">Directory refreshed <span class="live-updated"></span></div>
<div class="panel" style="margin-top:24px">
<label for="directory-search"><strong>Search {len(rows):,} software guides</strong></label>
<input id="directory-search" class="search" type="search" placeholder="Search tools, categories, or use cases…" autocomplete="off">
<div class="pills" id="category-pills">{pills}</div>
<p class="muted" id="result-count" aria-live="polite"></p>
<div class="cards" id="results"></div>
</div>
<div class="ad-unit" aria-label="Advertisement"></div>
</main>
{footer(year)}
<script>
(function(){{
  'use strict';
  var data={json_script(search_data)};
  var input=document.getElementById('directory-search');
  var results=document.getElementById('results');
  var count=document.getElementById('result-count');
  var active='*';
  var limit=80;
  function render(){{
    var q=input.value.trim().toLocaleLowerCase();
    var found=data.filter(function(item){{
      var categoryOk=active==='*'||item.category===active;
      var hay=(item.title+' '+item.category+' '+item.description).toLocaleLowerCase();
      return categoryOk&&(!q||hay.indexOf(q)!==-1);
    }});
    count.textContent=found.length.toLocaleString()+' matching guides'+(found.length>limit?' · showing first '+limit:'');
    results.textContent='';
    found.slice(0,limit).forEach(function(item){{
      var card=document.createElement('article');card.className='guide-card';
      var h=document.createElement('h3');var a=document.createElement('a');
      a.href=item.url;a.textContent=item.title;h.appendChild(a);
      var p=document.createElement('p');p.textContent=item.description;
      var meta=document.createElement('small');meta.textContent=item.category;
      card.appendChild(h);card.appendChild(p);card.appendChild(meta);results.appendChild(card);
    }});
  }}
  input.addEventListener('input',render);
  document.getElementById('category-pills').addEventListener('click',function(event){{
    var button=event.target.closest('button[data-category]');if(!button)return;
    active=button.getAttribute('data-category');
    document.querySelectorAll('.pill').forEach(function(p){{
      p.setAttribute('aria-pressed',p===button?'true':'false');
    }});
    render();
  }});
  render();
}})();
</script>"""
    head = base_head(
        f"Software Alternatives Directory ({year}) | Compare SaaS Tools",
        "Discover software alternatives, compare competitors, and find the right tools for your team.",
        canonical, homepage_schema,
    )
    return head + "<body>" + body + "</body></html>", search_data


def sitemap_xml(urls, today):
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}urlset")
    for url in urls:
        node = ET.SubElement(root, f"{{{namespace}}}url")
        ET.SubElement(node, f"{{{namespace}}}loc").text = url
        ET.SubElement(node, f"{{{namespace}}}lastmod").text = today
        ET.SubElement(node, f"{{{namespace}}}changefreq").text = "weekly"
        ET.SubElement(node, f"{{{namespace}}}priority").text = "0.8"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_sitemaps(out_dir, urls, today):
    chunks = [urls[i:i + SITEMAP_LIMIT] for i in range(0, len(urls), SITEMAP_LIMIT)]
    # The requested two sitemap files are always present, including small sites.
    while len(chunks) < 2:
        chunks.append([])
    for index, chunk in enumerate(chunks, start=1):
        (out_dir / f"sitemap-{index}.xml").write_bytes(sitemap_xml(chunk, today))
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}sitemapindex")
    for index in range(1, len(chunks) + 1):
        node = ET.SubElement(root, f"{{{ns}}}sitemap")
        ET.SubElement(node, f"{{{ns}}}loc").text = f"{BASE_URL}/sitemap-{index}.xml"
        ET.SubElement(node, f"{{{ns}}}lastmod").text = today
    (out_dir / "sitemap.xml").write_bytes(
        ET.tostring(root, encoding="utf-8", xml_declaration=True)
    )


def build(root):
    write_netlify_config(root)
    rows = read_rows(root / CSV_FILE)
    out_dir = root / OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    year = dt.datetime.now().year
    today = dt.date.today().isoformat()

    urls = [BASE_URL + "/"]
    for row in rows:
        page_dir = out_dir / row["slug"]
        page_dir.mkdir(parents=True, exist_ok=True)
        page_url = f"{BASE_URL}/{quote(row['slug'], safe='-')}/"
        (page_dir / "index.html").write_text(
            make_page(row, rows, year), encoding="utf-8"
        )
        urls.append(page_url)

    homepage, search_data = make_homepage(rows, year)
    (out_dir / "index.html").write_text(homepage, encoding="utf-8")
    (out_dir / "search-index.json").write_text(
        json.dumps(search_data, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    write_sitemaps(out_dir, urls, today)
    (out_dir / "robots.txt").write_text(
        "User-agent: *\nAllow: /\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n",
        encoding="utf-8",
    )
    (out_dir / KEY_FILE).write_text(INDEXNOW_KEY, encoding="utf-8")
    print(f"Built {len(rows)} guide pages plus homepage in {out_dir}/")


def load_submitted(path):
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Cannot read valid IndexNow state file {path}: {exc}") from exc
    if isinstance(data, list):
        values = data
    elif isinstance(data, dict):
        values = data.get("submitted_urls", [])
    else:
        raise ValueError("submitted_urls.json must contain a list or an object with submitted_urls.")
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError("submitted_urls in state file must be a list of URL strings.")
    return set(values)


def get_site_urls(out_dir):
    """Read sitemap locations so the ping queue exactly matches generated URLs."""
    sitemap = out_dir / "sitemap.xml"
    if not sitemap.exists():
        raise FileNotFoundError(f"{sitemap} not found. Run python build.py before --ping.")
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    index = ET.parse(sitemap).getroot()
    urls = []
    for node in index.findall("sm:sitemap/sm:loc", ns):
        child = node.text or ""
        request = urllib.request.Request(child, method="GET")
        # The sitemap index is local output; map its known filename to local disk.
        filename = child.rsplit("/", 1)[-1]
        local_file = out_dir / filename
        if not local_file.is_file():
            continue
        tree = ET.parse(local_file).getroot()
        urls.extend((loc.text or "") for loc in tree.findall("sm:url/sm:loc", ns))
    return list(dict.fromkeys(urls))


def ping_indexnow(root):
    out_dir = root / OUTPUT_DIR
    key_file = out_dir / KEY_FILE
    if not key_file.is_file() or key_file.read_text(encoding="utf-8").strip() != INDEXNOW_KEY:
        raise RuntimeError(f"IndexNow key file missing or invalid: {key_file}")
    urls = get_site_urls(out_dir)
    submitted_path = root / STATE_FILE
    submitted = load_submitted(submitted_path)
    batch = [url for url in urls if url not in submitted][:DRIP_BATCH_SIZE]
    if not batch:
        print("IndexNow queue is empty; all generated URLs have been submitted.")
        return

    payload = {
        "host": DOMAIN,
        "key": INDEXNOW_KEY,
        "keyLocation": f"{BASE_URL}/{KEY_FILE}",
        "urlList": batch,
    }
    request = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.getcode()
            response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise RuntimeError(f"IndexNow HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"IndexNow request failed: {exc}") from exc

    if status not in (200, 202):
        raise RuntimeError(f"IndexNow returned unexpected HTTP status {status}.")
    submitted.update(batch)
    submitted_path.write_text(
        json.dumps({"submitted_urls": sorted(submitted)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"IndexNow accepted {len(batch)} URLs (HTTP {status}). "
          f"{max(0, len(urls) - len(submitted))} URLs remain.")


def main():
    parser = argparse.ArgumentParser(description="Build the Software Alternatives Atlas static site.")
    parser.add_argument("--ping", action="store_true",
                        help="Submit the next unsubmitted URL batch to IndexNow.")
    args = parser.parse_args()
    root = Path.cwd()
    if args.ping:
        ping_indexnow(root)
    else:
        build(root)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
