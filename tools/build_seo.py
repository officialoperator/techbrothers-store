#!/usr/bin/env python3
"""
Techbrothers SEO build.

Reads the phone list (LIST, COLOURS, START/END, FEATURED) straight from index.html and writes:
  - media/phones/*.webp, media/logo.png, favicon.png, apple-touch-icon.png  (once: moves embedded images out of index.html)
  - one page per model:  /iphone-13/index.html, /iphone-16-pro-max/index.html, ...
  - homepage SEO blocks (between <!--SEO:...--> markers): head tags + JSON-LD, and a plain-HTML price list
  - sitemap.xml, robots.txt, 404.html

Run after changing prices or models in index.html:
    python3 tools/build_seo.py
Only facts from the store and the client's Warranty & Return Policy are used.
"""
import base64, html, json, os, re, sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://techbrothers.co.za"
BRAND = "Techbrothers.za"
PHONE = "+27679503488"
WA = "https://wa.me/27679503488"
SAME_AS = ["https://www.instagram.com/techbrothers.za", "https://www.tiktok.com/@OFFICIALTechbrothersza"]
POLICY = "/warranty-and-returns.pdf"
SAST = timezone(timedelta(hours=2))
TODAY = datetime.now(SAST).date().isoformat()

def rd(p): return open(os.path.join(ROOT, p), encoding="utf-8").read()
def wr(p, s):
    full = os.path.join(ROOT, p); os.makedirs(os.path.dirname(full) or ROOT, exist_ok=True)
    open(full, "w", encoding="utf-8").write(s)
def esc(s): return html.escape(str(s), quote=True)
def money(n): return "R" + f"{n:,}"
def slug(m): return re.sub(r"[^a-z0-9]+", "-", m.lower()).strip("-")

# ---------------------------------------------------------------- read the store data
src = rd("index.html")

def js_block(name, open_ch, close_ch):
    i = src.index(f"var {name} = {open_ch}")
    j = i; depth = 0
    for k in range(src.index(open_ch, i), len(src)):
        c = src[k]
        if c == open_ch: depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0: return src[src.index(open_ch, i):k + 1]
    raise ValueError(name)

LIST = []
for row in re.findall(r"\{s:'[^}]*\}", js_block("LIST", "[", "]")):
    d = dict(re.findall(r"(\w+):\s*('[^']*'|\d+)", row))
    LIST.append({k: (v.strip("'") if v.startswith("'") else int(v)) for k, v in d.items()})
COLOURS = {m: re.findall(r"\['([^']+)','#", body)
           for m, body in re.findall(r"'([^']+)':\s*\[(.*?)\]\s*\]", js_block("COLOURS", "{", "}"), re.S)}
PHOTO_FOR = dict(re.findall(r"'([^']+)':'([^']+)'", js_block("PHOTO_FOR", "{", "}")))
FEATURED = re.search(r"var FEATURED = '([^']+)'", src).group(1)
y, mo, d, h, mi, s_ = map(int, re.search(r"var END\s*=\s*Date\.UTC\(([^)]*)\)", src).group(1).split(","))
END = datetime(y, mo + 1, d, h, mi, s_, tzinfo=timezone.utc).astimezone(SAST)
VALID_UNTIL = END.date().isoformat()
SALE_ON = datetime.now(SAST) <= END
assert LIST and COLOURS, "could not read LIST/COLOURS from index.html"

COND = {"pre": ("Certified pre-owned", "certified pre-owned", "pre-owned", "https://schema.org/UsedCondition"),
        "neu": ("Brand new", "brand new", "brand-new", "https://schema.org/NewCondition")}
WARRANTY = {"pre": "30-day limited warranty", "neu": "6-month limited warranty"}

def store_id(e, c): return f"{e['m']}-{e['gb']}gb-{COND[c][2]}".lower().replace(" ", "-")
def photo_key(e): return PHOTO_FOR.get(f"{e['m']} {e['gb']}GB") or PHOTO_FOR.get(e["m"])

# ---------------------------------------------------------------- 1. move embedded images to files (one time)
def extract_images():
    global src
    changed = False
    def save(rel, b64):
        full = os.path.join(ROOT, rel); os.makedirs(os.path.dirname(full), exist_ok=True)
        if not os.path.exists(full): open(full, "wb").write(base64.b64decode(b64))
    for key, typ, b64 in re.findall(r"^\s*'([a-z0-9-]+)':'data:image/(\w+);base64,([A-Za-z0-9+/=]+)'", src, re.M):
        rel = f"media/phones/{key}.{typ}"; save(rel, b64)
        src = src.replace(f"'data:image/{typ};base64,{b64}'", f"'{rel}'"); changed = True
    m = re.search(r'<link rel="icon" type="image/png" href="data:image/png;base64,([A-Za-z0-9+/=]+)">', src)
    if m: save("favicon.png", m.group(1)); src = src.replace(m.group(0), '<link rel="icon" type="image/png" href="/favicon.png">'); changed = True
    m = re.search(r'<link rel="apple-touch-icon" href="data:image/png;base64,([A-Za-z0-9+/=]+)">', src)
    if m: save("apple-touch-icon.png", m.group(1)); src = src.replace(m.group(0), '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'); changed = True
    for b64 in set(re.findall(r'<img class="logo" src="data:image/png;base64,([A-Za-z0-9+/=]+)"', src)):
        save("media/logo.png", b64); src = src.replace(f"data:image/png;base64,{b64}", "/media/logo.png"); changed = True
    return changed

# ---------------------------------------------------------------- shared bits
MODELS = []   # one entry per model, in store order
for e in LIST:
    if not MODELS or MODELS[-1]["m"] != e["m"]:
        MODELS.append({"m": e["m"], "s": e["s"], "slug": slug(e["m"]), "rows": []})
    MODELS[-1]["rows"].append(e)
for M in MODELS:
    M["pre"] = min([r["pre"] for r in M["rows"] if "pre" in r] or [0])
    M["neu"] = min([r["neu"] for r in M["rows"] if "neu" in r] or [0])
    M["from"] = min(x for x in (M["pre"], M["neu"]) if x)
    M["img"] = photo_key(M["rows"][0])
    M["storages"] = sorted({r["gb"] for r in M["rows"]})
LOWEST = min(M["from"] for M in MODELS)

def img_size(rel):
    try:
        from PIL import Image
        return Image.open(os.path.join(ROOT, rel.lstrip("/"))).size
    except Exception:
        return (474, 640)

def abs_img(key): return f"{SITE}/media/phones/{key}.webp" if key else f"{SITE}/media/og.jpg"

RETURN_POLICY = {"@type": "MerchantReturnPolicy", "applicableCountry": "ZA",
                 "returnPolicyCategory": "https://schema.org/MerchantReturnFiniteReturnWindow",
                 "merchantReturnDays": 14, "merchantReturnLink": SITE + POLICY}
ORG = {"@type": ["OnlineStore", "Organization"], "@id": SITE + "/#store", "name": BRAND, "alternateName": "Techbrothers",
       "url": SITE + "/", "logo": SITE + "/media/logo.png", "image": SITE + "/media/og.jpg",
       "slogan": "Where tech meets trust.", "telephone": PHONE, "areaServed": {"@type": "Country", "name": "South Africa"},
       "address": {"@type": "PostalAddress", "addressCountry": "ZA"},
       "currenciesAccepted": "ZAR", "paymentAccepted": "EFT (bank transfer)",
       "contactPoint": {"@type": "ContactPoint", "telephone": PHONE, "contactType": "sales", "areaServed": "ZA", "availableLanguage": ["en"]},
       "hasMerchantReturnPolicy": RETURN_POLICY, "sameAs": SAME_AS}

def ld(obj): return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "</script>"

FONT = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Inter+Tight:wght@600;700&display=swap" rel="stylesheet">')

PAGE_CSS = """
:root{--ground:#F2F2F0;--ink:#0B0B0C;--soft:#3A3A3C;--muted:#6E6E73;--line:rgba(0,0,0,.09);--red:#D92D20;--gold:#E3B23C;--gold-soft:#FAF3DC;--green:#17924a}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-text-size-adjust:100%}
body{font-family:Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--ground);color:var(--ink);line-height:1.55;font-size:16px}
a{color:inherit}
img{max-width:100%;display:block}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px}
.head{position:sticky;top:0;z-index:5;background:rgba(242,242,240,.98);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.head .wrap{display:flex;align-items:center;justify-content:space-between;gap:16px;height:64px}
.head img{height:40px;width:auto}
.head nav{display:flex;align-items:center;gap:22px;font-size:14px;font-weight:600}
.head nav a{text-decoration:none}
.pill{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:46px;padding:0 20px;border-radius:999px;font-weight:600;font-size:15px;text-decoration:none;white-space:nowrap;border:0}
.pill.dark{background:var(--ink);color:#fff}
.pill.gold{background:var(--gold);color:var(--ink)}
.pill.line{background:#fff;border:1px solid var(--line)}
.crumbs{font-size:13px;color:var(--muted);padding:18px 0 6px}
.crumbs a{text-decoration:none}
.crumbs a:hover{text-decoration:underline}
.hero{display:grid;grid-template-columns:1.05fr .95fr;gap:28px;align-items:center;padding:10px 0 26px}
.hero h1{font-family:"Inter Tight",Inter,sans-serif;font-weight:700;font-size:clamp(34px,5.4vw,58px);line-height:1.02;letter-spacing:-.04em}
.lead{font-size:18px;color:var(--soft);margin-top:14px;max-width:34em}
.from{display:flex;flex-wrap:wrap;gap:10px;margin-top:20px}
.from div{background:#fff;border:1px solid var(--line);border-radius:16px;padding:12px 16px}
.from small{display:block;font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.from b{font-family:"Inter Tight",Inter,sans-serif;font-size:26px;letter-spacing:-.02em}
.cta{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}
.photo{background:radial-gradient(circle at 50% 55%,#fff 0%,rgba(255,255,255,0) 66%);border-radius:28px;display:grid;place-items:center;min-height:320px;padding:20px}
.photo img{max-height:420px;width:auto;filter:drop-shadow(0 26px 24px rgba(0,0,0,.18))}
.sale-note{display:inline-block;background:var(--red);color:#fff;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;padding:6px 12px;border-radius:999px;margin-bottom:14px}
section.card{background:#fff;border:1px solid var(--line);border-radius:24px;padding:24px;margin:16px 0}
section.card h2{font-family:"Inter Tight",Inter,sans-serif;font-size:clamp(22px,3vw,28px);letter-spacing:-.03em;line-height:1.15;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:15px}
th,td{text-align:left;padding:12px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
td.p{font-weight:700;font-family:"Inter Tight",Inter,sans-serif;font-size:18px;white-space:nowrap}
td.o{text-align:right}
td.o a{display:inline-flex;align-items:center;min-height:40px;padding:0 16px;border-radius:999px;background:var(--ink);color:#fff;font-weight:600;font-size:14px;text-decoration:none;white-space:nowrap}
.save{display:inline-block;margin-left:6px;font-size:12px;font-weight:600;color:var(--green);background:#E8F6EC;border-radius:999px;padding:2px 8px;white-space:nowrap}
.note{font-size:13px;color:var(--muted);margin-top:12px}
ul.ticks{list-style:none;display:grid;gap:10px}
ul.ticks li{padding-left:28px;position:relative}
ul.ticks li::before{content:"";position:absolute;left:0;top:.35em;width:16px;height:16px;border-radius:50%;background:#E8F6EC;box-shadow:inset 0 0 0 5px #E8F6EC,inset 0 0 0 16px var(--green)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips span{background:var(--ground);border-radius:999px;padding:6px 12px;font-size:14px}
details{border-bottom:1px solid var(--line);padding:14px 0}
details:last-child{border-bottom:0}
summary{cursor:pointer;font-weight:600;list-style:none;display:flex;justify-content:space-between;gap:12px}
summary::-webkit-details-marker{display:none}
summary::after{content:"+";font-size:20px;line-height:1;color:var(--muted)}
details[open] summary::after{content:"\\2212"}
details p{margin-top:8px;color:var(--soft)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}
.grid a{display:flex;align-items:center;gap:12px;background:var(--ground);border-radius:16px;padding:10px 12px;text-decoration:none}
.grid img{width:44px;height:56px;object-fit:contain}
.grid b{display:block;font-size:15px}
.grid span{font-size:13px;color:var(--muted)}
.foot{padding:34px 0 50px;color:var(--muted);font-size:14px}
.foot .cols{display:flex;flex-wrap:wrap;justify-content:space-between;gap:20px}
.foot a{text-decoration:none}
.foot a:hover{text-decoration:underline}
.foot nav{display:flex;flex-wrap:wrap;gap:8px 18px}
@media (max-width:820px){
  .hero{grid-template-columns:1fr}
  .photo{order:-1;min-height:260px}
  .photo img{max-height:300px}
  .head nav .hide-m{display:none}
}
@media (max-width:560px){
  section.card{padding:18px;border-radius:20px}
  th.c,td.c{display:none}
  table,tbody{display:block}
  thead{display:none}
  tbody tr{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;row-gap:4px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)}
  tbody td{display:block;padding:0;border:0;min-width:0}
  tbody td:first-child{grid-column:1;grid-row:1}
  tbody td.p{grid-column:1;font-size:17px}
  td.pre::before,td.neu::before{display:inline-block;min-width:78px;font-family:Inter,sans-serif;font-size:13px;font-weight:500;color:var(--muted)}
  td.pre::before{content:"Pre-owned"}
  td.neu::before{content:"New"}
  tbody td.o{grid-column:2;grid-row:1/span 3;align-self:center}
  .save{display:table;margin:4px 0 0}
  td.o a{padding:0 18px;min-height:42px}
}
"""

def head_html(title, desc, path, extra=""):
    url = SITE + path
    return f"""<!DOCTYPE html>
<html lang="en-ZA">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
<meta name="robots" content="index, follow, max-image-preview:large">
<meta property="og:type" content="website">
<meta property="og:site_name" content="{BRAND}">
<meta property="og:locale" content="en_ZA">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#F2F2F0">
<link rel="icon" type="image/png" href="/favicon.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
{FONT}
{extra}
<style>{PAGE_CSS}</style>
</head>
<body>
<header class="head"><div class="wrap">
  <a href="/" aria-label="{BRAND} home"><img src="/media/logo.png" alt="{BRAND}" width="96" height="40"></a>
  <nav><a class="hide-m" href="/#shop">Shop</a><a class="hide-m" href="/iphone-prices/">All prices</a><a class="pill dark" href="{WA}" rel="noopener">Contact us</a></nav>
</div></header>
<main class="wrap">
"""

SB_URL = re.search(r"var SB_URL = '([^']+)'", src).group(1)
SB_KEY = re.search(r"var SB_KEY = '([^']+)'", src).group(1)
# Same visit tracker as the store (no cookies, no personal data). Shares the tab session with the store,
# so a Google visitor who lands here and then orders is credited to Google, not "Direct".
TRACKER = """<script>(function(){try{
if(navigator.webdriver||/bot|crawl|spider|slurp|preview|facebookexternalhit|lighthouse|headless/i.test(navigator.userAgent))return;
var L=localStorage,S=sessionStorage,v=L.getItem('tb_vid');if(!v){v=Date.now().toString(36)+Math.random().toString(36).slice(2,12);L.setItem('tb_vid',v);}
var r='';try{if(document.referrer)r=new URL(document.referrer).hostname.replace(/^www\\./,'');}catch(_){}
if(r===location.hostname.replace(/^www\\./,''))r='';
var q=new URLSearchParams(location.search),t=(q.get('src')||q.get('utm_source')||'').trim().slice(0,40);
function f(h){if(!h)return'';if(/instagram/.test(h))return'Instagram';if(/facebook|fb\\.com|fb\\.me/.test(h))return'Facebook';if(/whatsapp|wa\\.me/.test(h))return'WhatsApp';
if(/google\\./.test(h))return'Google';if(/tiktok/.test(h))return'TikTok';if(/(^|\\.)t\\.co$|twitter|x\\.com/.test(h))return'X';if(/bing|yahoo|duckduckgo/.test(h))return'Search (other)';return h;}
var s=S.getItem('tb_src');if(!s){s=t||(q.get('fbclid')?'Facebook':q.get('gclid')?'Google Ads':'')||f(r)||'Direct';S.setItem('tb_src',s);}
if(S.getItem('tb_visit'))return;S.setItem('tb_visit','1');
var d=/iPad|Tablet/i.test(navigator.userAgent)?'Tablet':/Mobi|Android|iPhone/i.test(navigator.userAgent)?'Phone':'Computer';
fetch('""" + SB_URL + """/rest/v1/rpc/log_visit',{method:'POST',keepalive:true,headers:{'apikey':'""" + SB_KEY + """','Authorization':'Bearer """ + SB_KEY + """','Content-Type':'application/json'},
body:JSON.stringify({p_visitor:v,p_event:'visit',p_source:s,p_ref:r||null,p_path:location.pathname,p_device:d,p_model:null,p_order:null})}).catch(function(){});
}catch(_){}})();</script>"""

def foot_html(track=True):
    links = "".join(f'<a href="/{M["slug"]}/">{esc(M["m"])}</a>' for M in MODELS)
    return f"""</main>
<footer class="foot"><div class="wrap">
  <div class="cols">
    <div><b style="color:var(--ink)">{BRAND}</b><br>Certified pre-owned and brand new iPhones.<br>Nationwide delivery across South Africa.<br>
      <a href="{WA}" rel="noopener">Contact us</a> &middot; <a href="{POLICY}">Warranty &amp; Returns</a></div>
    <nav aria-label="iPhone models">{links}</nav>
  </div>
  <p style="margin-top:20px">Prices in South African rand. Apple and iPhone are trademarks of Apple Inc. {BRAND} is an independent retailer.</p>
</div></footer>
""" + (TRACKER if track else "") + """
</body></html>
"""

# ---------------------------------------------------------------- 2. model pages
def model_page(M):
    m, rows = M["m"], M["rows"]
    conds = [c for c in ("pre", "neu") if M[c]]
    has_both = len(conds) == 2
    cols = COLOURS.get(m, [])
    stor = " and ".join(f"{g}GB" for g in M["storages"])
    if has_both:
        title = f"{m} Price in South Africa | Pre-owned & New | {BRAND}"
        h1 = f"{m} price in South Africa"
        kind = "certified pre-owned and brand new"
    elif conds == ["pre"]:
        title = f"Pre-owned {m} Price in South Africa | {BRAND}"
        h1 = f"Pre-owned {m} price in South Africa"
        kind = "certified pre-owned"
    else:
        title = f"Brand New {m} Price in South Africa | {BRAND}"
        h1 = f"Brand new {m} price in South Africa"
        kind = "brand new"
    # search phrase first; brand only if the title still fits Google's ~60 characters (site name shows separately)
    core = {"both": f"{m} Price in South Africa, Pre-owned & New", "pre": f"Pre-owned {m} Price in South Africa",
            "neu": f"Brand New {m} Price in South Africa"}["both" if has_both else conds[0]]
    title = core + " | Techbrothers" if len(core) + 15 <= 60 else core
    parts = []
    if M["pre"]: parts.append(f"certified pre-owned from {money(M['pre'])}")
    if M["neu"]: parts.append(f"brand new from {money(M['neu'])}")
    desc = f"{m} ({stor}) at {BRAND}: {' and '.join(parts)}. Warranty included, nationwide delivery, order online."
    lead = (f"Buy the {m} from {BRAND}: {' or '.join(parts)}. "
            f"Every order comes with a warranty and nationwide delivery, and a Techbrothers representative confirms stock and colour before it ships.")

    # price rows
    trs, offers = [], []
    for r in rows:
        for c in ("pre", "neu"):
            if c not in r: continue
            save = ""
            if c == "pre" and r.get("neu") and r["neu"] > r["pre"]:
                save = f'<span class="save">{money(r["neu"] - r["pre"])} less than new</span>'
            oid = store_id(r, c)
            trs.append(f'<tr><td><b>{r["gb"]}GB</b><br><span style="font-size:13px;color:var(--muted);white-space:nowrap">{COND[c][0]}</span></td>'
                       f'<td class="c">{WARRANTY[c].capitalize()}</td><td class="p">{money(r[c])}{save}</td>'
                       f'<td class="o"><a href="/#{oid}" aria-label="Order {esc(m)} {r["gb"]}GB {COND[c][1]}">Order</a></td></tr>')
            offers.append({"@type": "Offer", "name": f"{m} {r['gb']}GB, {COND[c][1]}", "sku": oid, "url": f"{SITE}/{M['slug']}/",
                           "price": f"{r[c]:.2f}", "priceCurrency": "ZAR", "priceValidUntil": VALID_UNTIL,
                           "itemCondition": COND[c][3], "availability": "https://schema.org/InStock",
                           "seller": {"@id": SITE + "/#store"}, "hasMerchantReturnPolicy": RETURN_POLICY})

    # FAQ (only facts from the store + policy)
    price_lines = "; ".join(f"{r['gb']}GB {COND[c][1]} {money(r[c])}" for r in rows for c in ("pre", "neu") if c in r)
    faq = [(f"How much is the {m} at {BRAND}?",
            f"Current prices: {price_lines}. Prices are in South African rand and valid until {END.strftime('%-d %B %Y')}."),
           (f"What warranty comes with the {m}?",
            " ".join(([f"Certified pre-owned phones come with a 30-day limited warranty."] if M["pre"] else []) +
                     ([f"Brand new sealed phones come with a 6-month limited warranty."] if M["neu"] else []) +
                     ["The warranty covers hardware faults such as charging, speaker and microphone faults that were not caused by damage. See the full Warranty & Returns policy for details."])),
           ("Can I return or exchange the phone?",
            "Returns or exchanges are accepted within 14 days of purchase for a verified technical fault that was not caused by the customer. A Techbrothers-approved technician inspects the phone first."),
           ("Do you deliver across South Africa?",
            "Yes. Techbrothers delivers nationwide, to your door anywhere in South Africa."),
           ("How do I order and pay?",
            f"Choose your storage, condition and colour, add your name and number, then either pay by EFT straight away (the bank details and your order reference show on screen) or place the order and a Techbrothers representative contacts you to confirm stock and colour and arrange payment and delivery.")]
    if cols:
        faq.append((f"Which colours is the {m} available in?",
                    f"You can choose {', '.join(cols[:-1]) + ' or ' + cols[-1] if len(cols) > 1 else cols[0]} when you order. A Techbrothers representative confirms the colour is in stock before your phone ships."))
    if has_both:
        diff = M["neu"] - M["pre"]
        faq.insert(1, (f"Should I buy the {m} pre-owned or brand new?",
                       f"A certified pre-owned {m} starts at {money(M['pre'])}, {money(diff)} less than brand new at {money(M['neu'])}. Brand new phones are sealed and come with a longer 6-month warranty; pre-owned phones come with a 30-day warranty."))

    # related models: same series first, then neighbours
    idx = MODELS.index(M)
    rel = [x for x in MODELS if x["s"] == M["s"] and x is not M]
    for x in (MODELS[idx - 1] if idx else None, MODELS[idx + 1] if idx + 1 < len(MODELS) else None,
              MODELS[idx - 2] if idx > 1 else None, MODELS[idx + 2] if idx + 2 < len(MODELS) else None):
        if x and x not in rel and x is not M: rel.append(x)
    rel = rel[:6]

    img_rel = f"/media/phones/{M['img']}.webp" if M["img"] else "/media/og.jpg"
    IMG_W, IMG_H = img_size(img_rel)
    schema = [
        {"@context": "https://schema.org", "@type": "Product", "@id": f"{SITE}/{M['slug']}/#product", "name": m,
         "description": desc, "image": abs_img(M["img"]), "brand": {"@type": "Brand", "name": "Apple"},
         "category": "Mobile phones", "color": ", ".join(cols) if cols else None,
         "offers": offers if len(offers) > 1 else offers[0]},
        {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE + "/"},
            {"@type": "ListItem", "position": 2, "name": "iPhone prices", "item": SITE + "/iphone-prices/"},
            {"@type": "ListItem", "position": 3, "name": m, "item": f"{SITE}/{M['slug']}/"}]},
        {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]},
    ]
    if not cols: del schema[0]["color"]
    extra = (f'<meta property="og:image" content="{abs_img(M["img"])}">\n<link rel="preload" as="image" href="{img_rel}">\n'
             + "\n".join(ld(x) for x in schema))
    first = rows[0]; first_c = "pre" if "pre" in first else "neu"
    from_boxes = "".join(f'<div><small>{COND[c][0]}</small><b>from {money(M[c])}</b></div>' for c in conds)
    out = head_html(title, desc, f"/{M['slug']}/", extra)
    out += f"""<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> &rsaquo; <a href="/iphone-prices/">iPhone prices</a> &rsaquo; <span aria-current="page">{esc(m)}</span></nav>
<div class="hero">
  <div>
    {'<span class="sale-note">Black Friday Month price</span>' if SALE_ON else ''}
    <h1>{esc(h1)}</h1>
    <p class="lead">{esc(lead)}</p>
    <div class="from">{from_boxes}</div>
    <div class="cta"><a class="pill dark" href="/#{store_id(first, first_c)}">Order the {esc(m)}</a><a class="pill line" href="{WA}" rel="noopener">Speak to a representative</a></div>
  </div>
  <div class="photo"><img src="{img_rel}" alt="Apple {esc(m)}" width="{IMG_W}" height="{IMG_H}" fetchpriority="high"></div>
</div>

<section class="card" aria-labelledby="prices">
  <h2 id="prices">{esc(m)} prices</h2>
  <table>
    <thead><tr><th>Storage &amp; condition</th><th class="c">Warranty</th><th>Price</th><th class="o"><span style="position:absolute;left:-9999px">Order</span></th></tr></thead>
    <tbody>{''.join(trs)}</tbody>
  </table>
  <p class="note">Prices in South African rand, valid until {END.strftime('%-d %B %Y')}. A Techbrothers representative confirms stock and colour before your phone ships.</p>
</section>

<section class="card" aria-labelledby="included">
  <h2 id="included">What you get with every {esc(m)}</h2>
  <ul class="ticks">
    {('<li><b>30-day limited warranty</b> on certified pre-owned phones.</li>' if M['pre'] else '')}
    {('<li><b>6-month limited warranty</b> on brand new sealed phones.</li>' if M['neu'] else '')}
    <li><b>14-day returns or exchanges</b> for a verified technical fault.</li>
    <li><b>Nationwide delivery</b> to your door, anywhere in South Africa.</li>
    <li><b>A real person checks your order</b>: a Techbrothers representative confirms stock and colour before it ships.</li>
    <li><b>Pay by EFT</b> with your own order reference, or place your order and be contacted.</li>
  </ul>
  <p class="note"><a href="{POLICY}">Read the full Warranty &amp; Returns policy (PDF)</a></p>
</section>
{f'''<section class="card" aria-labelledby="colours">
  <h2 id="colours">{esc(m)} colours</h2>
  <div class="chips">{"".join(f"<span>{esc(c)}</span>" for c in cols)}</div>
  <p class="note">Pick your colour when you order.</p>
</section>''' if cols else ''}
<section class="card" aria-labelledby="faq">
  <h2 id="faq">{esc(m)} questions</h2>
  {''.join(f'<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>' for q, a in faq)}
</section>

<section class="card" aria-labelledby="more">
  <h2 id="more">Compare other iPhones</h2>
  <div class="grid">{''.join(f'<a href="/{x["slug"]}/">' + (f'<img src="/media/phones/{x["img"]}.webp" alt="Apple {esc(x["m"])}" width="44" height="56" loading="lazy">' if x["img"] else '') + f'<div><b>{esc(x["m"])}</b><span>from {money(x["from"])}</span></div></a>' for x in rel)}</div>
  <p class="note"><a href="/iphone-prices/">See every iPhone price</a></p>
</section>
"""
    return out + foot_html()

# ---------------------------------------------------------------- 3. all-prices hub page
def prices_page():
    title = f"iPhone Prices in South Africa | Pre-owned & New from {money(LOWEST)}"
    desc = (f"Every iPhone price at {BRAND}, from the iPhone X to the latest Pro Max. Pre-owned and brand new from "
            f"{money(LOWEST)}, with warranty and nationwide delivery.")
    rows = ""
    for M in MODELS:
        rows += (f'<tr><td><a href="/{M["slug"]}/"><b>{esc(M["m"])}</b></a><br><span style="font-size:13px;color:var(--muted)">'
                 f'{" / ".join(str(g) + "GB" for g in M["storages"])}</span></td>'
                 f'<td class="p pre">{money(M["pre"]) if M["pre"] else "&mdash;"}</td>'
                 f'<td class="p neu">{money(M["neu"]) if M["neu"] else "&mdash;"}</td>'
                 f'<td class="o"><a href="/{M["slug"]}/">View</a></td></tr>')
    schema = [{"@context": "https://schema.org", "@type": "ItemList", "name": "iPhone prices at " + BRAND,
               "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": f"{SITE}/{M['slug']}/", "name": M["m"]}
                                   for i, M in enumerate(MODELS)]},
              {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                  {"@type": "ListItem", "position": 1, "name": "Home", "item": SITE + "/"},
                  {"@type": "ListItem", "position": 2, "name": "iPhone prices", "item": SITE + "/iphone-prices/"}]}]
    extra = f'<meta property="og:image" content="{SITE}/media/og.jpg">\n' + "\n".join(ld(x) for x in schema)
    out = head_html(title, desc, "/iphone-prices/", extra)
    out += f"""<nav class="crumbs" aria-label="Breadcrumb"><a href="/">Home</a> &rsaquo; <span aria-current="page">iPhone prices</span></nav>
<div style="padding:10px 0 8px">
  {'<span class="sale-note">Black Friday Month prices</span>' if SALE_ON else ''}
  <div class="hero" style="grid-template-columns:1fr;padding-bottom:6px"><div>
    <h1>iPhone prices in South Africa</h1>
    <p class="lead">Every iPhone at {BRAND}, certified pre-owned and brand new, from {money(LOWEST)}. Each phone comes with a warranty and nationwide delivery, and a Techbrothers representative confirms stock and colour before it ships.</p>
  </div></div>
</div>
<section class="card" aria-labelledby="t">
  <h2 id="t">Prices from (lowest storage)</h2>
  <table>
    <thead><tr><th>Model</th><th>Pre-owned</th><th>Brand new</th><th class="o"><span style="position:absolute;left:-9999px">Details</span></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="note">Prices in South African rand, valid until {END.strftime('%-d %B %Y')}. Pre-owned phones carry a 30-day limited warranty and brand new sealed phones a 6-month limited warranty. <a href="{POLICY}">Warranty &amp; Returns policy</a>.</p>
</section>
<section class="card" aria-labelledby="h">
  <h2 id="h">Certified pre-owned or brand new?</h2>
  <p style="color:var(--soft)">A certified pre-owned iPhone costs less: on most models it is several hundred rand cheaper than the same phone brand new. Brand new phones are sealed and come with a longer 6-month warranty. Both are covered by 14-day returns for a verified technical fault and delivered nationwide.</p>
  <div class="cta"><a class="pill dark" href="/#pre-owned">Shop certified pre-owned</a><a class="pill line" href="/#brand-new">Shop brand new</a></div>
</section>
"""
    return out + foot_html()

# ---------------------------------------------------------------- 4. homepage blocks
def set_block(s, name, content, anchor_before=None, anchor_after=None):
    start, end = f"<!--SEO:{name}-->", f"<!--/SEO:{name}-->"
    block = f"{start}\n{content}\n{end}"
    if start in s:
        return re.sub(re.escape(start) + r".*?" + re.escape(end), lambda _: block, s, flags=re.S)
    if anchor_before:
        assert s.count(anchor_before) == 1, anchor_before; return s.replace(anchor_before, block + "\n" + anchor_before)
    assert s.count(anchor_after) == 1, anchor_after; return s.replace(anchor_after, anchor_after + "\n" + block)

def homepage():
    global src
    title = f"Black Friday iPhone Deals South Africa | {BRAND}" if SALE_ON else f"iPhone Deals South Africa from {money(LOWEST)} | {BRAND}"
    desc = (f"{'Black Friday Month iPhone deals' if SALE_ON else 'iPhone deals'} in South Africa: certified pre-owned and brand new iPhones from "
            f"{money(LOWEST)}. Warranty included, nationwide delivery, order online.")
    src = re.sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", src, count=1, flags=re.S)
    src = re.sub(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{esc(desc)}">', src, count=1)
    src = re.sub(r'<meta property="og:title" content="[^"]*">', f'<meta property="og:title" content="{esc(title)}">', src, count=1)
    src = re.sub(r'<meta property="og:description" content="[^"]*">', f'<meta property="og:description" content="{esc(desc)}">', src, count=1)
    # replace the old single Store JSON-LD with the generated one
    src = re.sub(r'<script type="application/ld\+json">\s*\{"@context":"https://schema.org","@type":"Store".*?</script>\s*', "", src, count=1, flags=re.S)
    featured = next((M for M in MODELS if M["m"] == FEATURED), None)
    graph = {"@context": "https://schema.org", "@graph": [
        ORG,
        {"@type": "WebSite", "@id": SITE + "/#website", "url": SITE + "/", "name": BRAND, "inLanguage": "en-ZA", "publisher": {"@id": SITE + "/#store"}},
        {"@type": "WebPage", "@id": SITE + "/#webpage", "url": SITE + "/", "name": title, "description": desc, "isPartOf": {"@id": SITE + "/#website"},
         "about": {"@id": SITE + "/#store"}, "primaryImageOfPage": SITE + "/media/og.jpg", "inLanguage": "en-ZA"},
        {"@type": "ItemList", "name": "iPhones at " + BRAND, "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": f"{SITE}/{M['slug']}/", "name": M["m"]} for i, M in enumerate(MODELS)]}]}
    headblock = (f'<meta name="robots" content="index, follow, max-image-preview:large">\n<meta property="og:site_name" content="{BRAND}">\n'
                 f'<meta property="og:locale" content="en_ZA">\n<meta name="theme-color" content="#F2F2F0">\n'
                 + (f'<link rel="preload" as="image" href="media/phones/{featured["img"]}.webp" fetchpriority="high">\n' if featured and featured["img"] else "")
                 + ld(graph))
    src = set_block(src, "HEAD", headblock, anchor_before="</head>")
    if '<h1 id="hx-h">Black Friday</h1>' in src:
        src = src.replace('<h1 id="hx-h">Black Friday</h1>', '<h1 id="hx-h">Black Friday<span class="hx-kw"> iPhone deals in South Africa</span></h1>')
        src = src.replace('<p class="hx-sub">A month of iPhone deals from R2,899. Delivered anywhere in South Africa.</p>',
                          '<p class="hx-sub">Certified pre-owned and brand new iPhones from R2,899, with warranty and delivery anywhere in South Africa.</p>')
        src = src.replace("</style>", ".hx h1 .hx-kw{display:block;font-size:max(.2em,20px);letter-spacing:-.03em;line-height:1.15;margin-top:.3em;white-space:normal}\n</style>", 1)
    # crawlable price list near the bottom of the homepage
    items = "".join(f'<a href="/{M["slug"]}/"><b>{esc(M["m"])}</b><span>'
                    + " &middot; ".join(x for x in ((f"Pre-owned from {money(M['pre'])}" if M["pre"] else ""),
                                                    (f"New from {money(M['neu'])}" if M["neu"] else "")) if x)
                    + "</span></a>" for M in MODELS)
    block = f"""<section class="wrap seo-prices" aria-labelledby="seo-prices-h">
  <h2 id="seo-prices-h">iPhone prices in South Africa</h2>
  <p>Every model on the list, certified pre-owned and brand new. Tap a model for storage options, warranty and delivery details.</p>
  <div class="seo-grid">{items}</div>
  <p class="seo-all"><a href="/iphone-prices/">See all iPhone prices</a> &middot; <a href="{POLICY}">Warranty &amp; Returns policy</a></p>
</section>
<style>
.seo-prices{{padding-top:20px;padding-bottom:30px}}
.seo-prices h2{{font-family:var(--display);font-weight:700;font-size:clamp(26px,3.4vw,40px);letter-spacing:-.04em;line-height:1.05}}
.seo-prices>p{{color:var(--soft);margin-top:10px;max-width:40em}}
.seo-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:18px}}
.seo-grid a{{display:block;background:#fff;border:1px solid var(--line);border-radius:16px;padding:12px 14px;text-decoration:none;color:var(--ink)}}
.seo-grid a:hover{{border-color:rgba(0,0,0,.25)}}
.seo-grid b{{display:block;font-size:15px}}
.seo-grid span{{display:block;font-size:13px;color:var(--muted);margin-top:2px}}
.seo-all{{margin-top:14px;font-size:14px}}
.seo-all a{{color:var(--ink)}}
</style>"""
    src = set_block(src, "PRICES", block, anchor_before="</main>")

# ---------------------------------------------------------------- 5. technical files
def technical():
    urls = [("/", "1.0"), ("/iphone-prices/", "0.9")] + [(f"/{M['slug']}/", "0.8") for M in MODELS]
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    sm += "".join(f"  <url><loc>{SITE}{u}</loc><lastmod>{TODAY}</lastmod><priority>{p}</priority></url>\n" for u, p in urls)
    sm += "</urlset>\n"
    wr("sitemap.xml", sm)
    wr("robots.txt", f"User-agent: *\nAllow: /\nDisallow: /tools/\n\nSitemap: {SITE}/sitemap.xml\n")
    nf = head_html(f"Page not found | {BRAND}", "This page does not exist. Browse iPhone deals at Techbrothers.za.", "/404.html",
                   '<meta name="robots" content="noindex">')
    nf += f"""<div class="hero" style="grid-template-columns:1fr;padding:60px 0"><div>
<h1>Page not found</h1><p class="lead">The page you were looking for isn't here. The deals are, though.</p>
<div class="cta"><a class="pill dark" href="/">Go to the store</a><a class="pill line" href="/iphone-prices/">All iPhone prices</a></div></div></div>"""
    wr("404.html", nf + foot_html(track=False))

# ---------------------------------------------------------------- run
if __name__ == "__main__":
    moved = extract_images()
    homepage()
    wr("index.html", src)
    for M in MODELS: wr(f"{M['slug']}/index.html", model_page(M))
    wr("iphone-prices/index.html", prices_page())
    technical()
    print(f"images moved: {moved}; models: {len(MODELS)}; sale on: {SALE_ON}; prices valid until {VALID_UNTIL}")
    print("pages:", ", ".join("/" + M["slug"] + "/" for M in MODELS))
