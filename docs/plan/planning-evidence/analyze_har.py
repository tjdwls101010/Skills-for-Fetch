#!/usr/bin/env python3
"""Analyze the FB recon HAR: extract GraphQL query friendly-names, doc_ids,
request token presence, and response JSON structure — STRUCTURE ONLY, no
personal post content is printed."""
import json, sys, urllib.parse
from collections import Counter

HAR = "/private/tmp/claude-501/-Users-seongjin-Documents-Coding-Skills-for-Fetch/9aa254c5-5b3a-4503-85b4-e57d03c05377/scratchpad/fb_recon.har"

with open(HAR, encoding="utf-8") as f:
    har = json.load(f)

entries = har["log"]["entries"]
gql = [e for e in entries if e["request"]["method"] == "POST"
       and e["request"]["url"].rstrip("/").endswith("/api/graphql")]

print(f"total entries: {len(entries)}  |  graphql POSTs: {len(gql)}")

friendly = Counter()
docids = {}          # friendly_name -> set of doc_ids
token_keys = Counter()
sample_vars = {}     # friendly_name -> variable KEY names only

for e in gql:
    post = e["request"].get("postData", {})
    text = post.get("text", "")
    # form-urlencoded params
    params = dict(urllib.parse.parse_qsl(text))
    fn = params.get("fb_api_req_friendly_name", "(none)")
    friendly[fn] += 1
    if "doc_id" in params:
        docids.setdefault(fn, set()).add(params["doc_id"])
    for tk in ("fb_dtsg", "jazoest", "lsd", "__spin_r", "variables", "doc_id", "server_timestamps"):
        if tk in params:
            token_keys[tk] += 1
    if fn not in sample_vars and "variables" in params:
        try:
            v = json.loads(params["variables"])
            sample_vars[fn] = sorted(v.keys())
        except Exception:
            pass

print("\n===== top GraphQL friendly-names (by frequency) =====")
for fn, c in friendly.most_common(20):
    ids = docids.get(fn, set())
    print(f"{c:4d}  {fn}   doc_id(s)={list(ids)[:2]}")

print("\n===== request token/param presence (across", len(gql), "graphql calls) =====")
for k, c in token_keys.most_common():
    print(f"  {k}: {c}/{len(gql)}")

# Pick timeline-related queries and show variable KEY names + response structure
print("\n===== variable key-names for timeline/feed queries =====")
for fn, keys in sample_vars.items():
    if any(w in fn.lower() for w in ("timeline", "feed", "profile")):
        print(f"  {fn}: vars={keys}")

print("\n===== response JSON top-level structure for a timeline query (keys only) =====")
def struct(o, depth=0, maxd=3):
    if depth > maxd:
        return "..."
    if isinstance(o, dict):
        return {k: struct(v, depth+1, maxd) for k, v in list(o.items())[:6]}
    if isinstance(o, list):
        return [struct(o[0], depth+1, maxd)] if o else []
    return type(o).__name__

shown = 0
for e in gql:
    post = e["request"].get("postData", {})
    params = dict(urllib.parse.parse_qsl(post.get("text", "")))
    fn = params.get("fb_api_req_friendly_name", "")
    if not any(w in fn.lower() for w in ("timeline", "feed")):
        continue
    body = e["response"].get("content", {}).get("text", "")
    if not body:
        continue
    # FB responses can be multiple JSON objects separated by newlines
    first = body.split("\n", 1)[0]
    try:
        obj = json.loads(first)
    except Exception:
        continue
    print(f"\n  query: {fn}  (response {len(body)} bytes)")
    print("  structure:", json.dumps(struct(obj), indent=1)[:1200])
    shown += 1
    if shown >= 2:
        break
if shown == 0:
    print("  (no timeline response body captured in HAR — responses may not be stored)")
