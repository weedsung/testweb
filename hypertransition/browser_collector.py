from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output_browser"
OUT.mkdir(parents=True, exist_ok=True)

KEYWORD = "NFT"
GEO = "KR"
START = "2019-01-01"
END = "2023-12-31"


def visible_candidates(page):
    selectors = [
        "button[aria-label*='Download' i]",
        "button[title*='Download' i]",
        "[data-tooltip*='Download' i]",
        ".widget-actions-item.export",
        "button.widget-actions-item",
    ]
    found = []
    for selector in selectors:
        locator = page.locator(selector)
        for idx in range(locator.count()):
            item = locator.nth(idx)
            try:
                if item.is_visible():
                    found.append((selector, idx, item))
            except Exception:
                pass
    return found


def main():
    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "keyword": KEYWORD,
        "geo": GEO,
        "start": START,
        "end": END,
        "status": "started",
        "network": [],
    }
    query = quote(KEYWORD)
    date = quote(f"{START} {END}")
    url = f"https://trends.google.com/trends/explore?date={date}&geo={GEO}&q={query}&hl=en-US"
    summary["url"] = url

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            accept_downloads=True,
            locale="en-US",
            timezone_id="UTC",
            viewport={"width": 1440, "height": 1200},
        )
        page = context.new_page()

        def on_response(response):
            target = response.url
            if any(token in target for token in ("widgetdata", "batchexecute", "/trends/api/")):
                record = {
                    "url": target[:1000],
                    "status": response.status,
                    "content_type": response.headers.get("content-type", ""),
                }
                try:
                    body = response.text()
                    record["body_prefix"] = body[:500]
                    if "multiline" in target or "batchexecute" in target:
                        index = len(summary["network"])
                        (OUT / f"network_{index}.txt").write_text(body, encoding="utf-8")
                except Exception as exc:
                    record["body_error"] = f"{type(exc).__name__}: {exc}"
                summary["network"].append(record)

        page.on("response", on_response)

        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=120_000)
            summary["navigation_status"] = response.status if response else None
            page.wait_for_timeout(15_000)

            for text in ("Accept all", "I agree", "Agree", "모두 동의"):
                button = page.get_by_role("button", name=text, exact=False)
                if button.count():
                    try:
                        button.first.click(timeout=5_000)
                        summary["consent_clicked"] = text
                        page.wait_for_timeout(8_000)
                        break
                    except Exception:
                        pass

            page.screenshot(path=str(OUT / "page.png"), full_page=True)
            (OUT / "page.html").write_text(page.content(), encoding="utf-8")
            summary["title"] = page.title()
            summary["final_url"] = page.url

            buttons = page.locator("button")
            button_meta = []
            for idx in range(min(buttons.count(), 100)):
                item = buttons.nth(idx)
                try:
                    button_meta.append({
                        "index": idx,
                        "text": item.inner_text()[:200],
                        "aria_label": item.get_attribute("aria-label"),
                        "title": item.get_attribute("title"),
                        "class": item.get_attribute("class"),
                        "visible": item.is_visible(),
                    })
                except Exception:
                    pass
            summary["buttons"] = button_meta

            candidates = visible_candidates(page)
            summary["candidate_count"] = len(candidates)
            summary["candidates"] = [
                {"selector": selector, "index": idx}
                for selector, idx, _ in candidates
            ]

            downloaded = False
            errors = []
            for selector, idx, item in candidates:
                try:
                    with page.expect_download(timeout=30_000) as info:
                        item.click(timeout=10_000)
                    download = info.value
                    destination = OUT / "NFT_KR_segment1.csv"
                    download.save_as(str(destination))
                    summary["download_suggested_filename"] = download.suggested_filename
                    summary["download_bytes"] = destination.stat().st_size
                    downloaded = True
                    break
                except Exception as exc:
                    errors.append(f"{selector}[{idx}]: {type(exc).__name__}: {exc}")

            summary["download_errors"] = errors
            summary["status"] = "ok" if downloaded else "no_download"
        except PlaywrightTimeoutError as exc:
            summary["status"] = "timeout"
            summary["error"] = str(exc)
        except Exception as exc:
            summary["status"] = "error"
            summary["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            (OUT / "browser_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
