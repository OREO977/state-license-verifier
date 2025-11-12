# ut_adapter.py
# Real Utah lookup using Playwright (headless browser).
# Works best when deployed with a Docker image that includes Playwright browsers.

from playwright.sync_api import sync_playwright
import datetime as dt
import re
from typing import List, Dict

UT_URL = "https://secure.utah.gov/llv/search/index.html#"

def _split_name(full_name: str):
    parts = full_name.strip().split()
    if len(parts) == 1:
        return "", parts[0]
    # naive split: first token(s) as first, last token as last
    first = " ".join(parts[:-1])
    last = parts[-1]
    return first, last

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()

def _parse_date(s: str):
    s = _clean(s)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None

def verify_ut(full_name: str) -> List[Dict]:
    """
    Opens Utah DOPL 'Licensee Lookup & Verification',
    performs a name search (Physician & Surgeon), clicks into matching result,
    and extracts license details.

    Returns a list of normalized license dicts (usually length 1).
    """
    first, last = _split_name(full_name)
    results: List[Dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()

        # 1) Go to the search page
        page.goto(UT_URL, wait_until="load")

        # 2) Click "Name Search" (site uses a tabbed UI)
        try:
            # Try a few ways to reach the Name Search tab
            # (the UI can vary; these are robust fallbacks)
            page.get_by_role("link", name=re.compile("Name Search", re.I)).click()
        except Exception:
            # Sometimes already on the Name Search tab; ignore
            pass

        # 3) Fill name fields.
        # The form typically has First/Last fields; if it's a single field,
        # we fall back to typing last name only.
        # Try common input names/labels:
        filled = False
        selectors = [
            ('input[name="firstName"]', first),
            ('input[name="lastName"]', last),
        ]

        for sel, val in selectors:
            try:
                el = page.locator(sel)
                if el.count() > 0 and val:
                    el.fill(val)
                    filled = True
            except Exception:
                pass

        if not filled:
            # fall back: try a generic input and just use last name
            try:
                page.locator("input[type='text']").first.fill(last)
            except Exception:
                pass

        # 4) Select Profession = Physician & Surgeon (MD path; DO is different wording)
        # Try common <select> names or a visible dropdown role.
        selected = False
        try:
            # Typical select element
            sel = page.locator("select[name='profession'], select[name='licenseType']")
            if sel.count() > 0:
                sel.select_option(label=re.compile("Physician.*Surgeon", re.I))
                selected = True
        except Exception:
            pass

        if not selected:
            # Some versions have radio-buttons or a fake dropdown. Try clicking visible text.
            try:
                page.get_by_text(re.compile("Physician.*Surgeon", re.I)).first.click()
            except Exception:
                pass

        # 5) Submit search (button often labeled "Search")
        try:
            page.get_by_role("button", name=re.compile("Search", re.I)).click()
        except Exception:
            # fallback: try pressing Enter in the last field
            try:
                page.keyboard.press("Enter")
            except Exception:
                pass

        # Wait for results to load (table or list)
        page.wait_for_timeout(1500)  # small pause for rendering
        # Heuristic: a result row should contain the last name
        try:
            # Click the first matching result row that contains the last name
            row = page.locator("text=" + last).first
            row.click()
        except Exception:
            # If no row clicked, there may be zero results
            ctx.close()
            browser.close()
            return []

        # 6) We should now be on a detail page. Extract key fields.
        page.wait_for_timeout(800)

        def val_right_of(label_regex: str) -> str:
            """
            Find the value element to the right/under a label text.
            We try multiple strategies to be resilient to small DOM changes.
            """
            try:
                # Strategy A: find a label cell and grab its nearest following sibling
                lab = page.locator(f"text=/{label_regex}/i").first
                # Try grabbing the next sibling text
                sib = lab.locator("xpath=following::*[1]")
                txt = sib.inner_text(timeout=1000)
                return _clean(txt)
            except Exception:
                pass
            return ""

        license_number = val_right_of(r"License Number|License #")
        status = val_right_of(r"Status")
        issue_date = _parse_date(val_right_of(r"Issue Date|Original Date"))
        expiry_date = _parse_date(val_right_of(r"Expiration|Expiry|Expires"))

        # If the page has a more structured table, try some table-based fallbacks:
        if not license_number:
            try:
                license_number = _clean(page.locator("css=td:has-text('License') + td, css=th:has-text('License') + td").first.inner_text())
            except Exception:
                pass
        if not status:
            try:
                status = _clean(page.locator("css=td:has-text('Status') + td, css=th:has-text('Status') + td").first.inner_text())
            except Exception:
                pass

        record = {
            "full_name": full_name,
            "state": "UT",
            "license_number": license_number or "UNKNOWN",
            "status": status or None,
            "issue_date": issue_date,
            "expiry_date": expiry_date,
            "source_uri": UT_URL,
            "last_verified_at": dt.datetime.utcnow(),
        }
        results.append(record)

        ctx.close()
        browser.close()
    return results
