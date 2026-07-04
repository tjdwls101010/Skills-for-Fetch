#!/usr/bin/env python3
"""Discover the REAL nested JSON paths to Facebook post fields from a live
capture. Prints PATH TEMPLATES (list indices collapsed to []) + tiny redacted
samples so we can hand the implementer a concrete extractor without leaking
personal content."""
import sys, json, re
from collections import defaultdict
PROFILE_DIR = sys.argv[1]; PROFILE_URL = sys.argv[2]
from scrapling.fetchers import DynamicSession

TARGET_KEYS = {"text","name","creation_time","url","wwwURL","permalink","title",
               "message","story","id","uri","subtitle_text"}

def scroll(page):
    page.wait_for_timeout(3500)
    for _ in range(6):
        page.mouse.wheel(0, 3200)
        page.wait_for_timeout(2500)
    return page

def iter_json_objects(body):
    """FB bodies are newline-delimited concatenations of JSON objects."""
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            pass

paths = defaultdict(lambda: {"count": 0, "sample": ""})
def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        if o:
            walk(o[0], f"{path}[]")
    else:
        # leaf: record if the last key looks post-relevant
        last = path.rsplit(".", 1)[-1].replace("[]", "")
        if last in TARGET_KEYS and isinstance(o, str) and o.strip():
            rec = paths[path]
            rec["count"] += 1
            if not rec["sample"]:
                s = re.sub(r"\s+", " ", o)[:28]
                rec["sample"] = s + ("…" if len(o) > 28 else "")

with DynamicSession(user_data_dir=PROFILE_DIR, headless=True, capture_xhr=r"graphql") as s:
    page = s.fetch(PROFILE_URL, page_action=scroll, load_dom=True, timeout=120000)
    html = page.html_content if hasattr(page,"html_content") else ""
    print("login-wall:", ("you must log in" in html.lower() or "login_form" in html.lower()))
    xhrs = getattr(page,"captured_xhr",[]) or []
    print("captured graphql responses:", len(xhrs))
    total_objs = 0; profile_queries = 0
    for x in xhrs:
        body = x.body if isinstance(getattr(x,"body",None),str) else (x.body.decode("utf-8","replace") if getattr(x,"body",None) else "")
        if "ProfileCometTimeline" in body or "ProfileCometTilesFeed" in body or "timeline_list_feed_units" in body or "timeline_feed_units" in body:
            profile_queries += 1
        for obj in iter_json_objects(body):
            total_objs += 1
            walk(obj)
    print(f"total JSON objects across bodies: {total_objs}  |  profile/timeline-bearing bodies: {profile_queries}")
    print("\n=== discovered post-field path templates (path : count : redacted-sample) ===")
    # show the most frequent, likely-real ones
    ranked = sorted(paths.items(), key=lambda kv: -kv[1]["count"])
    shown = 0
    for p, rec in ranked:
        # keep paths that look like they live under a feed/story container
        if any(w in p for w in ("feed","story","Story","comet_sections","actor","message","attachment","timeline")):
            print(f"{rec['count']:4d}  {p}   e.g. {rec['sample']!r}")
            shown += 1
        if shown >= 40:
            break
    if shown == 0:
        print("(no feed-container post paths found; dumping top target-key paths regardless)")
        for p, rec in ranked[:30]:
            print(f"{rec['count']:4d}  {p}   e.g. {rec['sample']!r}")
