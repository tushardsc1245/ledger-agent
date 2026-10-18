import re, json, calendar, requests, pandas as pd
from urllib.parse import quote
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

FULL_URL = "https://exam.sanand.workers.dev/questionData?email=25f1002656%40ds.study.iitm.ac.in&quizSign=JYPfa7A%2B%2BhrsLgCPAulJlljjx5M9Ich3baoAsOw%2BSQuh1nuPAG8MntleU5GRa9n5XG941w9aQky1Ppl%2Fzy2aHbOTHusca6%2BtgAd5wIG2i3rYIhnONlP4merzsD77rAkAMdhjfLAAe5dBtGlgYN8MNq%2Fiq%2FiswRrGSQixPClI3uKJgYUnrsiIO3CZRE9yi7txBetUggDA%2BYgA8LGhois3qz8QA%2B7hCjxd0qsCOxKC41IBgSdwjNvifrRuTw1Ikher9y9Z2i4%2F92KYoqKXwJA2lea4RX3jHCgqjktaisekXulM3zPk%2BejK9pGDOA%2BBvCbzcs1pd4xziXD6FPk2mVENQQ%3D%3D&questionId=q-ledger-agent-server&path=/"
BASE = FULL_URL.split("&path=")[0]

def api(path="/", **params):
    url = BASE + "&path=" + quote(path, safe="")
    for k, v in params.items():
        url += f"&{k}={v}"
    return requests.get(url, timeout=30)

# ---------- load + clean (once per instance) ----------
RATES = api("/rates").json()["usd_per_unit"]
raw = pd.DataFrame([json.loads(l) for l in api("/export").text.splitlines() if l.strip()])
df = raw.copy()
for c in ["created_at", "updated_at"]:
    df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601").dt.tz_convert("Asia/Kolkata")
df = df.sort_values("updated_at").drop_duplicates("id", keep="last")
df["status_c"] = df.status.astype(str).str.strip().str.lower()
df["region_c"] = df.region.astype(str).str.strip().str.lower()
df["usd"] = df.amount * df.currency.map(RATES)
df["date"] = df.created_at.dt.date
print("orders:", len(df), "paid total:", round(df.loc[df.status_c == "paid", "usd"].sum(), 2))

# ---------- engine ----------
def run(spec):
    x = df
    st = spec.get("status", "paid")
    if st and st != "any":
        x = x[x.status_c == st.lower()]
    if spec.get("region"):
        x = x[x.region_c == spec["region"].strip().lower()]
    if spec.get("product"):
        x = x[x["product"].str.lower() == spec["product"].strip().lower()]
    if spec.get("customer"):
        x = x[x.customer.str.lower() == spec["customer"].strip().lower()]
    if spec.get("date_from"):
        x = x[x.date >= pd.Timestamp(spec["date_from"]).date()]
    if spec.get("date_to"):
        x = x[x.date <= pd.Timestamp(spec["date_to"]).date()]
    agg = {"sum_usd": lambda s: s.usd.sum(), "avg_usd": lambda s: s.usd.mean(),
           "max_usd": lambda s: s.usd.max(), "min_usd": lambda s: s.usd.min(),
           "count": len, "sum_qty": lambda s: s.qty.sum(),
           "distinct_customers": lambda s: s.customer.nunique(),
           "distinct_products": lambda s: s["product"].nunique()}[spec["metric"]]
    g = spec.get("group_by")
    if g:
        res = {k: agg(sub) for k, sub in x.groupby(g)}
        if not res: return None
        return (min if spec.get("rank") == "bottom" else max)(res, key=res.get)
    v = agg(x)
    if pd.isna(v): return 0
    return round(float(v), 2) if spec["metric"].endswith("usd") else int(v)

# ---------- parser ----------
PRODUCTS = ["Blender","Grinder","Toaster","Juicer","Rice Cooker","Kettle","Mixer","Air Fryer"]
REGIONS = ["north","south","east","west","central"]
MN = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MN.update({"jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12})
MON_RE = r"\b(" + "|".join(sorted(MN, key=len, reverse=True)) + r")\b\.?,?\s*(20\d\d)?"
UNITS_RE = r"\bunits?\b|\bitems?\b|quantity|pieces"

def parse_dates(ql):
    iso = sorted(re.findall(r"20\d\d-\d\d-\d\d", ql))
    if iso: return iso[0], iso[-1]
    ys = re.findall(r"\b(20\d\d)\b", ql)
    dy = int(ys[0]) if ys else 2026
    mq = re.search(r"\bq([1-4])\b", ql)
    if mq:
        q = int(mq.group(1)); s, e = 3*q-2, 3*q
        return f"{dy}-{s:02d}-01", f"{dy}-{e:02d}-{calendar.monthrange(dy,e)[1]}"
    if re.search(r"first half|\bh1\b", ql): return f"{dy}-01-01", f"{dy}-06-30"
    if re.search(r"second half|\bh2\b", ql): return f"{dy}-07-01", f"{dy}-12-31"
    ms = []
    for m in re.finditer(MON_RE, ql):
        if m.group(1) == "may" and not m.group(2):
            before = ql[:m.start()].split()[-1:] or [""]
            if before[0] not in ("in","of","during","for","since","from","to","through","until","by"):
                continue
        ms.append((MN[m.group(1)], int(m.group(2)) if m.group(2) else dy))
    if ms:
        (m1, y1), (m2, y2) = ms[0], ms[-1]
        return f"{y1}-{m1:02d}-01", f"{y2}-{m2:02d}-{calendar.monthrange(y2,m2)[1]}"
    if ys: return f"{dy}-01-01", f"{dy}-12-31"
    return None, None

GROUP_PATS = [
    r"\b(?:which|what)\s+(?:\w+\s+)?(product|region|customer)s?\b",
    r"\b(?:top|best|biggest|largest|worst|bottom)(?:[- ]\w+)?\s+(product|region|customer)s?\b",
    r"\b(product|region|customer)s?\s+(?:has|had|with|generated|earned|brought|made|sold|bought|ordered|spent|did)\b.*\b(?:most|highest|largest|least|lowest|fewest|biggest|best|worst)\b",
]

def parse(q):
    rawq = q.lower()
    ql = rawq.replace("-", " ")
    spec = {}
    if "refund" in ql: spec["status"] = "refunded"
    elif re.search(r"\bvoid", ql): spec["status"] = "void"
    elif re.search(r"all orders|any status|regardless of status|every order", ql): spec["status"] = "any"
    else: spec["status"] = "paid"
    for r in REGIONS:
        if re.search(rf"\b{r}\b", ql): spec["region"] = r.capitalize()
    for p in PRODUCTS:
        if re.search(rf"\b{p.lower()}s?\b", ql): spec["product"] = p
    c = re.search(r"\bc\d{3,5}\b", ql)
    if c: spec["customer"] = c.group(0).upper()
    a, b = parse_dates(rawq)
    if a: spec["date_from"], spec["date_to"] = a, b
    grp = None
    for pat in GROUP_PATS:
        m = re.search(pat, ql)
        if m: grp = m.group(1); break
    if not grp and re.search(r"\bwho\b", ql): grp = "customer"
    if grp:
        spec["group_by"] = grp
        spec["rank"] = "bottom" if re.search(r"least|lowest|fewest|worst|smallest|bottom|minimum", ql) else "top"
        spec.pop(grp, None)
        if re.search(UNITS_RE, ql) and "unit price" not in ql: spec["metric"] = "sum_qty"
        elif re.search(r"orders|purchases|refunds|how many", ql): spec["metric"] = "count"
        else: spec["metric"] = "sum_usd"
        return spec
    money = re.search(r"dollar|usd|\$", ql)
    if re.search(r"(unique|distinct|different)\s+customers|how many customers", ql): spec["metric"] = "distinct_customers"
    elif re.search(r"(unique|distinct|different)\s+products|how many products", ql): spec["metric"] = "distinct_products"
    elif re.search(UNITS_RE, ql) and "unit price" not in ql: spec["metric"] = "sum_qty"
    elif re.search(r"averag|\bavg\b|\bmean\b|typical", ql): spec["metric"] = "avg_usd"
    elif re.search(r"largest|biggest|highest|maximum|\bmax\b|most expensive", ql): spec["metric"] = "max_usd"
    elif re.search(r"smallest|lowest|minimum|\bmin\b|cheapest", ql): spec["metric"] = "min_usd"
    elif re.search(r"how many|number of|\bcount\b", ql) and not money: spec["metric"] = "count"
    else: spec["metric"] = "sum_usd"
    return spec

def answer(q):
    spec = parse(q)
    return spec, run(spec)

# ---------- server ----------
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

async def _ask(req: Request):
    try:
        body = await req.json()
        _, a = answer(body.get("question", ""))
    except Exception as e:
        print("ERR", e)
        a = None
    return {"answer": a}

@app.post("/")
async def ask_root(req: Request):
    return await _ask(req)

@app.post("/api/index")
async def ask_api(req: Request):
    return await _ask(req)

@app.get("/")
@app.get("/api/index")
def health():
    return {"ok": True}
