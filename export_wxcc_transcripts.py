#!/usr/bin/env python3
"""
Export Webex Contact Center call transcripts for a date range (one-off, manual run).

Steps:
  1. Search API (GraphQL)  -> list handled telephony task IDs + call metadata in the window
  2. POST /v1/captures/query -> get short-lived (<=60 min) download URLs for each task's transcript
  3. Download each transcript immediately and save as <taskId>.json, plus an index.csv of metadata
     (queue, agent, wrap-up, talk time, and Topic Analytics topicName / contactDriver per call)

Requirements:  pip install requests
Auth:          a Webex access token for an admin / Service App with cjp:config_read (+ cjp-analyzer:read)
               export WXCC_TOKEN="..."   (never hard-code it)

Usage:
  python export_wxcc_transcripts.py --org <ORG_ID> --api https://api.wxcc-eu1.cisco.com \
      --from 2026-04-21 --to 2026-10-21 --out ./transcripts

Notes:
  * Resumable: tasks already saved in --out are skipped, so you can re-run after errors/token expiry.
  * The captures response schema can vary by tenant/version, so transcript URLs are located by
    walking the JSON for URL fields under transcript/transcription keys. Run with --dry-run-one
    first to inspect a real response and confirm.
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timezone, timedelta
import requests

GQL = """
query Tasks($from: Long!, $to: Long!, $after: String) {
  taskDetails(from: $from, to: $to,
    filter: { and: [ { channelType: { equals: telephony } }, { isContactHandled: { equals: true } } ] },
    pagination: { cursor: $after }) {
    tasks { id createdTime endedTime direction
            lastQueue { name } lastAgent { name } lastEntryPoint { name }
            lastWrapupCodeName connectedDuration topicName contactDriver }
    pageInfo { endCursor hasNextPage }
  }
}"""


def ms(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def call(session, method, url, **kw):
    """HTTP with retry on 429/5xx (honours Retry-After)."""
    for attempt in range(8):
        r = session.request(method, url, timeout=120, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 2 ** attempt))
            print(f"  {r.status_code} - retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def list_tasks(s, api, org, start, end):
    """Page through the Search API one day at a time (keeps each query small)."""
    day = timedelta(days=1)
    cur = datetime.fromtimestamp(start / 1000, timezone.utc)
    stop = datetime.fromtimestamp(end / 1000, timezone.utc)
    while cur < stop:
        nxt = min(cur + day, stop)
        after = None
        while True:
            body = {"query": GQL, "variables": {"from": int(cur.timestamp() * 1000),
                                                 "to": int(nxt.timestamp() * 1000), "after": after}}
            data = call(s, "POST", f"{api}/search?orgId={org}", json=body).json()
            if data.get("errors"):
                raise RuntimeError(data["errors"])
            td = data["data"]["taskDetails"]
            yield from td["tasks"]
            if not td["pageInfo"]["hasNextPage"]:
                break
            after = td["pageInfo"]["endCursor"]
        cur = nxt


def find_transcript_urls(obj, in_transcript=False):
    """Recursively collect download URLs that sit under a transcript/transcription key."""
    urls = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            flag = in_transcript or "transcri" in k.lower()
            if flag and isinstance(v, str) and v.startswith("http"):
                urls.append(v)
            else:
                urls.extend(find_transcript_urls(v, flag))
    elif isinstance(obj, list):
        for v in obj:
            urls.extend(find_transcript_urls(v, in_transcript))
    return urls


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--org", required=True)
    p.add_argument("--api", required=True, help="e.g. https://api.wxcc-eu1.cisco.com (your region)")
    p.add_argument("--from", dest="frm", required=True, help="YYYY-MM-DD (UTC, inclusive)")
    p.add_argument("--to", required=True, help="YYYY-MM-DD (UTC, exclusive)")
    p.add_argument("--out", default="./transcripts")
    p.add_argument("--batch", type=int, default=10, help="task IDs per captures query")
    p.add_argument("--dry-run-one", action="store_true", help="print one raw captures response and stop")
    a = p.parse_args()

    token = os.environ.get("WXCC_TOKEN")
    if not token:
        sys.exit("Set WXCC_TOKEN in the environment.")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    os.makedirs(a.out, exist_ok=True)

    print("Listing tasks...")
    tasks = list(list_tasks(s, a.api, a.org, ms(a.frm), ms(a.to)))
    print(f"  {len(tasks)} handled telephony calls found")

    index_path = os.path.join(a.out, "index.csv")
    new_index = not os.path.exists(index_path)
    with open(index_path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new_index:
            w.writerow(["taskId", "createdTime", "endedTime", "direction", "entryPoint", "queue", "agent",
                        "wrapUp", "talkSec", "topicName", "contactDriver", "transcriptFile"])

        todo = [t for t in tasks if not os.path.exists(os.path.join(a.out, f"{t['id']}.json"))]
        print(f"  {len(todo)} still to download")
        for i in range(0, len(todo), a.batch):
            chunk = todo[i:i + a.batch]
            body = {"query": {"orgId": a.org, "taskIds": [t["id"] for t in chunk],
                              "urlExpiration": 60, "includeSegments": False}}
            resp = call(s, "POST", f"{a.api}/v1/captures/query", json=body).json()
            if a.dry_run_one:
                print(json.dumps(resp, indent=2)[:5000])
                return

            # map URLs back to task IDs
            by_task = {}
            for item in resp.get("data", resp if isinstance(resp, list) else []):
                tid = item.get("taskId") or item.get("id")
                by_task[tid] = find_transcript_urls(item)

            for t in chunk:
                urls = by_task.get(t["id"], [])
                fname = ""
                if urls:
                    content = call(requests.Session(), "GET", urls[0]).content  # pre-signed URL, no auth header
                    fname = f"{t['id']}.json"
                    with open(os.path.join(a.out, fname), "wb") as out:
                        out.write(content)
                iso = lambda v: datetime.fromtimestamp(v / 1000, timezone.utc).isoformat() if v else ""
                w.writerow([t["id"], iso(t.get("createdTime")), iso(t.get("endedTime")), t.get("direction"),
                            (t.get("lastEntryPoint") or {}).get("name"),
                            (t.get("lastQueue") or {}).get("name"), (t.get("lastAgent") or {}).get("name"),
                            t.get("lastWrapupCodeName"), round((t.get("connectedDuration") or 0) / 1000),
                            t.get("topicName"), t.get("contactDriver"),  # Topic Analytics (null = not analysed)
                            fname])
            fh.flush()
            print(f"  {min(i + a.batch, len(todo))}/{len(todo)}")
            time.sleep(0.5)  # be gentle with rate limits

    print(f"Done. Transcripts + index.csv in {a.out}")


if __name__ == "__main__":
    main()
