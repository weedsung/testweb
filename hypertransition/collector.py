from __future__ import annotations

import csv, json, random, statistics, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import requests

BASE = "https://trends.google.com/trends"
OUT = Path(__file__).resolve().parent / "output"
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
COUNTRIES = ["KR", "JP", "TW", "SG", "US", "GB"]
SEGMENTS = [("2019-01-01", "2023-12-31"), ("2022-01-01", "2026-06-30")]
KEYWORD = "NFT"
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/123.0 Safari/537.36",
]


def strip_xssi(text: str) -> str:
    text = text.strip()
    return text[4:].lstrip("\n") if text.startswith(")]}'") else text


def get_json(session: requests.Session, url: str, params: dict[str, Any], attempts: int = 5) -> dict[str, Any]:
    err: Exception | None = None
    for attempt in range(attempts):
        try:
            session.headers["User-Agent"] = random.choice(UAS)
            response = session.get(url, params=params, timeout=60)
            if response.status_code == 200:
                return json.loads(strip_xssi(response.text))
            err = RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
        except Exception as exc:
            err = exc
        time.sleep(min(90, 8 * (2 ** attempt)) + random.uniform(1, 4))
    raise RuntimeError(f"request failed: {err}")


def fetch_segment(session: requests.Session, geo: str, start: str, end: str):
    request = {
        "comparisonItem": [{"keyword": KEYWORD, "geo": geo, "time": f"{start} {end}"}],
        "category": 0,
        "property": "",
    }
    explore = get_json(session, f"{BASE}/api/explore", {
        "hl": "en-US", "tz": "0",
        "req": json.dumps(request, separators=(",", ":"), ensure_ascii=False),
    })
    widget = next((w for w in explore.get("widgets", []) if w.get("id") == "TIMESERIES"), None)
    if not widget:
        raise RuntimeError(f"TIMESERIES missing: {[w.get('id') for w in explore.get('widgets', [])]}")
    payload = get_json(session, f"{BASE}/api/widgetdata/multiline", {
        "hl": "en-US", "tz": "0",
        "req": json.dumps(widget["request"], separators=(",", ":"), ensure_ascii=False),
        "token": widget["token"],
    })
    rows = []
    for point in payload.get("default", {}).get("timelineData", []):
        rows.append({
            "date": datetime.fromtimestamp(int(point["time"]), tz=timezone.utc).date().isoformat(),
            "interest": point.get("value", [None])[0],
            "is_partial": bool(point.get("isPartial", False)),
        })
    return rows, {
        "geo": geo, "start": start, "end": end,
        "resolution": widget["request"].get("resolution"), "points": len(rows),
    }


def write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def scale_ratio(previous: dict[str, float], current: dict[str, float]):
    ratios = [previous[d] / current[d] for d in set(previous) & set(current)
              if previous[d] >= 5 and current[d] >= 5]
    return (statistics.median(ratios), len(ratios)) if len(ratios) >= 8 else (1.0, len(ratios))


def stitch(segment_rows):
    merged: dict[str, list[float]] = {}
    reference: dict[str, float] = {}
    scale_log = []
    for idx, rows in enumerate(segment_rows):
        current = {r["date"]: float(r["interest"]) for r in rows if r["interest"] is not None}
        scale, overlap = (1.0, 0) if idx == 0 else scale_ratio(reference, current)
        scale_log.append({"segment": idx + 1, "scale": scale, "overlap_points": overlap})
        for date, value in current.items():
            merged.setdefault(date, []).append(value * scale)
        reference = {d: statistics.mean(v) for d, v in merged.items()}
    rows = [{"date": d, "interest_unscaled": statistics.mean(v)} for d, v in sorted(merged.items())]
    maximum = max((r["interest_unscaled"] for r in rows), default=0)
    for row in rows:
        row["interest"] = 0 if maximum <= 0 else round(100 * row["interest_unscaled"] / maximum, 4)
    return rows, scale_log


def main():
    session = requests.Session()
    session.headers.update({"Accept-Language": "en-US,en;q=0.9", "Referer": f"{BASE}/explore"})
    summary = {"started_at": datetime.now(timezone.utc).isoformat(), "keyword": KEYWORD, "countries": {}}
    try:
        session.get("https://trends.google.com/", timeout=30)
    except Exception as exc:
        summary["cookie_warning"] = str(exc)

    for geo in COUNTRIES:
        result = {"status": "pending", "segments": []}
        collected = []
        try:
            for index, (start, end) in enumerate(SEGMENTS, start=1):
                rows, meta = fetch_segment(session, geo, start, end)
                write_csv(RAW / f"NFT_{geo}_segment{index}.csv", rows, ["date", "interest", "is_partial"])
                result["segments"].append(meta); collected.append(rows)
                time.sleep(random.uniform(7, 12))
            stitched, scales = stitch(collected)
            write_csv(OUT / f"NFT_{geo}.csv", stitched, ["date", "interest", "interest_unscaled"])
            result.update({"status": "ok", "scales": scales, "points": len(stitched)})
        except Exception as exc:
            result.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        summary["countries"][geo] = result
        (OUT / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(random.uniform(10, 16))

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["ok_count"] = sum(v["status"] == "ok" for v in summary["countries"].values())
    summary["error_count"] = sum(v["status"] == "error" for v in summary["countries"].values())
    (OUT / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
