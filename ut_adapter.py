# ut_adapter.py
# Utah lookup (Playwright) with robust result picking and popup handling.

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import datetime as dt
import re
from typing import List, Dict, Optional

UT_URL = "https://secure.utah.gov/llv/search/index.html#"

def _tokens(name: str):
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    if len(parts) == 1:
        return "", parts[0]
    return " ".join(parts[:-1]), parts[-1]

def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _parse_date(s: str):
    s = _clean(s)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None

def _value_for(page, label_regex: str) -> str:
    """Find value text near a label, with multiple fallbacks."""
    # Strategy A: label node → following sibling
    try:
        lab = page.locator(f"text=/{label_regex}/i").first
        sib = lab.locator("xpath=following::*[1]")
        txt = _clean(sib.inner_text(timeout=1200))
        if txt:
            return txt
    except Exception:
        pass
    # Strategy B: table cell pattern
    try:
        td = page.locator(
            "css=td:has-text(/%s/i) + td, th:has-text(/%s/i) + td" % (label_regex, label_regex)
        ).first
        txt = _clean(td.inner_text(timeout=1200))
        if txt:
            return txt
    except Exception:
        pass
    # Strategy C: definition list pattern
    try:
        dd = page.locator(
            "css=dt:has-text(/%s/i) + dd" % label_regex
        ).first
        txt = _clean(dd.inner_text(timeout=1200))
        if txt:
            return txt
    except Exception:
        pass
    return ""

def verify_ut(full_name: str) -> List[Dict]:
    first, last = _tokens(full_name)
    print(f"[UT] lookup start → first='{first}' last='{last}'")

    results: List[Dict] = []
    ACTION_TIMEOUT = 10_000  # 10s per action
    with sync_playwright() as p:
        browser = None
        ctx = None
        page = None
        try:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(ACTION_TIMEOUT)

            # 1) open search
            print("[UT] goto search page…")
            page.goto(UT_URL, wait_until="domcontentloaded", timeout=ACTION_TIMEOUT)

            # close any cookie/ack modals quickly (best-effort)
            try:
                page.get_by_role("button", name=re.compile("(accept|agree|close)", re.I)).first.click(timeout=1500)
                print("[UT] cookie/ack closed.")
            except Exception:
                pass

            # ensure we're on "Name Search"
            try:
                page.get_by_role("link", name=re.compile("Name Search", re.I)).click(timeout=2000)
                print("[UT] name search tab selected.")
            except Exception:
                print("[UT] name search tab not clickable (probably already active).")

            # 2) fill fields — last name is the most reliable filter
            filled = False
            try:
                if first:
                    loc = page.locator("input[name='firstName'], input[aria-label*='First']").first
                    if loc.count() > 0:
                        loc.fill(first)
                        filled = True
                        print("[UT] first name filled.")
            except Exception:
                pass
            try:
                if last:
                    loc = page.locator("input[name='lastName'], input[aria-label*='Last']").first
                    if loc.count() > 0:
                        loc.fill(last)
                        filled = True
                        print("[UT] last name filled.")
            except Exception:
                pass
            if not filled and last:
                # fallback: first visible text box
                try:
                    page.locator("input[type='text']").first.fill(last)
                    print("[UT] generic text field filled with last name.")
                except Exception:
                    pass

            # Profession selection is not always required; try best-effort and continue if missing.
            try:
                sel = page.locator("select[name='profession'], select[name='licenseType']").first
                if sel.count() > 0:
                    sel.select_option(label=re.compile("Physician.*Surgeon", re.I))
                    print("[UT] profession set: Physician & Surgeon.")
                else:
                    # sometimes labels are clickable tags/radios
                    page.get_by_text(re.compile("Physician.*Surgeon", re.I)).first.click(timeout=1500)
                    print("[UT] profession set via text click.")
            except Exception:
                print("[UT] profession not explicitly set (continuing).")

            # 3) submit search
            submitted = False
            try:
                page.get_by_role("button", name=re.compile("Search", re.I)).first.click(timeout=2000)
                submitted = True
                print("[UT] Search clicked.")
            except Exception:
                pass
            if not submitted:
                try:
                    page.keyboard.press("Enter")
                    print("[UT] Enter pressed to submit.")
                except Exception:
                    pass

            # give results time to render
            page.wait_for_timeout(1500)

            # 4) pick the correct person in results
            # We try to find an element that contains both tokens (first + last)
            candidates = page.locator("a, td, div, span").filter(has_text=re.compile(last, re.I))
            count = min(candidates.count(), 20)
            print(f"[UT] result candidates found: {count}")
            picked = False

            def name_match(txt: str) -> bool:
                t = txt.lower()
                return (first.lower() in t if first else True) and (last.lower() in t if last else True)

            for i in range(count):
                txt = _clean(candidates.nth(i).inner_text(timeout=1200))
                if not txt:
                    continue
                if name_match(txt):
                    # Click; handle popup/new tab too
                    print(f"[UT] clicking candidate: '{txt[:80]}'")
                    with page.expect_popup() as maybe_popup:
                        try:
                            candidates.nth(i).click(timeout=2000)
                        except Exception:
                            # if click didn’t create a popup, just continue
                            pass
                    try:
                        new_page = maybe_popup.value
                        page = new_page  # switch to the detail page in popup
                        page.set_default_timeout(ACTION_TIMEOUT)
                        print("[UT] detail opened in popup.")
                    except Exception:
                        print("[UT] detail opened in same tab.")
                    picked = True
                    break

            if not picked:
                print("[UT] no matching row with both name tokens — returning empty.")
                return []

            # 5) parse detail
            page.wait_for_timeout(800)

            lic_no = _value_for(page, r"License Number|License #|License No")
            status = _value_for(page, r"Status")
            issue_date = _parse_date(_value_for(page, r"Issue Date|Original Date"))
            expiry_date = _parse_date(_value_for(page, r"Expiration|Expiry|Expires"))

            record = {
                "full_name": full_name,
                "state": "UT",
                "license_number": lic_no or "UNKNOWN",
                "status": status or None,
                "issue_date": issue_date,
                "expiry_date": expiry_date,
                "source_uri": UT_URL,
                "last_verified_at": dt.datetime.utcnow(),
            }
            print(f"[UT] parsed record → lic={record['license_number']} status={record['status']} expiry={record['expiry_date']}")
            results.append(record)
            return results

        except PWTimeout:
            print("[UT] timeout while interacting with page — returning empty.")
            return []
        except Exception as e:
            print(f"[UT] unexpected error: {e} — returning empty.")
            return []
        finally:
            try:
                if ctx: ctx.close()
            except Exception:
                pass
            try:
                if browser: browser.close()
            except Exception:
                pass
