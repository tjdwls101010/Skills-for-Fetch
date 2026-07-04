#!/usr/bin/env python3
"""Decisive POC: scrapling launches its OWN chromium against the persistent
logged-in profile (user_data_dir) and captures /api/graphql/ timeline bodies.
Structure/markers only — no personal content printed."""
import sys, json
from collections import Counter
PROFILE_DIR = sys.argv[1]; PROFILE_URL = sys.argv[2]
from scrapling.fetchers import DynamicSession

def scroll(page):
    page.wait_for_timeout(3000)
    for _ in range(4):
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(2500)
    return page

with DynamicSession(user_data_dir=PROFILE_DIR, headless=True,
                    capture_xhr=r"graphql") as s:
    page = s.fetch(PROFILE_URL, page_action=scroll, load_dom=True, timeout=90000)
    try: title = page.css("title::text").get() or ""
    except Exception: title = "?"
    html = page.html_content if hasattr(page,"html_content") else ""
    print("landed URL:", getattr(page,"url","?"))
    print("title:", title[:80])
    print("login-wall marker present:", ("you must log in" in html.lower() or "login_form" in html.lower()))
    xhrs = getattr(page,"captured_xhr",[]) or []
    print("captured_xhr total:", len(xhrs))
    names = Counter(); timeline_body=None
    for x in xhrs:
        b = x.body if isinstance(getattr(x,"body",None),str) else (x.body.decode("utf-8","replace") if getattr(x,"body",None) else "")
        for m in ("ProfileCometTimelineFeedRefetch","ProfileCometTilesFeedPagination","TimelineFeed"):
            if m in b: names[m]+=1
            if m.startswith("ProfileCometT") and timeline_body is None and b.strip().startswith("{"):
                timeline_body=b
    print("graphql bodies by marker:", dict(names))
    if timeline_body:
        obj=json.loads(timeline_body.split("\n",1)[0])
        def keys(o,d=0):
            if d>4: return "..."
            if isinstance(o,dict): return {k:keys(v,d+1) for k,v in list(o.items())[:5]}
            if isinstance(o,list): return [keys(o[0],d+1)] if o else []
            return type(o).__name__
        print("\nTIMELINE JSON STRUCTURE (keys only):")
        print(json.dumps(keys(obj),indent=1)[:1400])
        print("\n>>> SUCCESS: scrapling+user_data_dir captured LOGGED-IN timeline JSON")
    else:
        print("\n>>> no timeline body (check login persisted in profile)")
