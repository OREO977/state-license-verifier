# ut_adapter.py
# Utah lookup (Playwright) — iframe aware + robust selectors + verbose logs.

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Frame
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

def _value_for(container: Page | Frame, label_regex: str) -> str:
    """Find value text near a label within a page/frame."""
    # Strategy A: label then following sibling (works for many detail layouts)
    try:
        lab = container.locator(f"text=/{label_regex}/i").first
        sib = lab.locator("xpath=following::*[1]")
        txt = _clean(sib.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    # Strategy B: table cell pattern
    try:
        td = container.locator(
            "css=td:has-text(/%s/i) + td, th:has-text(/%s/i) + td" % (label_regex, label_regex)
        ).first
        txt = _clean(td.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    # Strategy C: definition list pattern
    try:
        dd = container.locator("css=dt:has-text(/%s/i) + dd" % label_regex).first
        txt = _clean(dd.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    return ""

def _find_search_frame(page: Page) -> Frame | Page:
    """Return the frame (or page) that actually contains the Name Search UI."""
    # If the app is single-page without iframes, just return page.
    # Otherwise, look for a frame that has 'Name Search' or the input fields.
    frames = page.frames
    print(f"[UT] frames detected: {len(frames)}")
    # Quick pass: look for frame with visible 'Name Search' tab or lastName input
    for f in frames:
        try:
            if f.locator("text=/Name\\s*Search/i").first.count():
                print("[UT] selected frame by 'Name Search' text.")
                return f
        except Exception:
            pass
        try:
            if f.locator("input[name='lastName']").first.count():
                print("[UT] selected frame by lastName input.")
                return f
        except Exception:
            pass
    # Fallback: return main page
    print("[UT] using main page as container.")
    return page

def verify_ut(full_name: str) -> List[Dict]:
    first, last = _tokens(full_name)
    print(f"[UT] lookup start → first='{first}' last='{last}'")

    results: List[Dict] = []
    ACTION_TIMEOUT = 12_000  # 12s per action

    with sync_playwright() as p:
        browser = None
        ctx = None
        page = None
        try:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(ACTION_TIMEOUT)

            # 1) open search shell
            print("[UT] goto search page…")
            page.goto(UT_URL, wait_until="domcontentloaded", timeout=ACTION_TIMEOUT)

            # close any cookie/ack modals on the shell
            try:
                page.get_by_role("button", name=re.compile("(accept|agree|close)", re.I)).first.click(timeout=1500)
                print("[UT] cookie/ack (shell) closed.")
            except Exception:
                pass

            # 2) find the real search container (may be an iframe)
            container = _find_search_frame(page)

            # if the container has its own cookie/ack/modal, close it too
            try:
                container.get_by_role("button", name=re.compile("(accept|agree|close)", re.I)).first.click(timeout=1500)
                print("[UT] cookie/ack (frame) closed.")
            except Exception:
                pass

            # make sure we are on "Name Search" inside the container
            try:
                container.get_by_role("link", name=re.compile("Name\\s*Search", re.I)).first.click(timeout=2500)
                print("[UT] Name Search tab selected.")
            except Exception:
                print("[UT] Name Search tab not clickable (likely already active).")

            # 3) fill FIRST and LAST name (with fallbacks)
            filled_any = False
            try:
                if first:
                    loc = container.locator("input[name='firstName'], input[aria-label*='First']").first
                    if loc.count() > 0:
                        loc.fill(first)
                        filled_any = True
                        print("[UT] first name filled.")
            except Exception:
                pass
            try:
                if last:
                    loc = container.locator("input[name='lastName'], input[aria-label*='Last']").first
                    if loc.count() > 0:
                        loc.fill(last)
                        filled_any = True
                        print("[UT] last name filled.")
            except Exception:
                pass
            if not filled_any and last:
                try:
                    container.locator("input[type='text']").first.fill(last)
                    print("[UT] generic text field filled with last name.")
                except Exception:
                    pass

            # 4) set Profession: Physician & Surgeon (best-effort)
            try:
                sel = container.locator("select[name='profession'], select[name='licenseType']").first
                if sel.count() > 0:
                    sel.select_option(label=re.compile("Physician.*Surgeon", re.I))
                    print("[UT] profession set via <select>.")
                else:
                    container.get_by_text(re.compile("Physician.*Surgeon", re.I)).first.click(timeout=2000)
                    print("[UT] profession set via text/radio.")
            except Exception:
                print("[UT] profession not explicitly set (continuing).")

            # 5) submit
            submitted = False
            try:
                container.get_by_role("button", name=re.compile("Search", re.I)).first.click(timeout=2500)
                submitted = True
                print("[UT] Search clicked.")
            except Exception:
                pass
            if not submitted:
                try:
                    container.keyboard.press("Enter")
                    submitted = True
                    print("[UT] Enter pressed to submit.")
                except Exception:
                    pass

            container.wait_for_timeout(1500)

            # 6) scan results INSIDE the container for our name
            # Many Utah implementations render a table with rows/links.
            # We look for anything containing the last name, then check it also contains the first.
            candidate_nodes = container.locator("a, td, div, span").filter(has_text=re.compile(last, re.I))
            n = candidate_nodes.count()
            print(f"[UT] result candidates (frame): {n}")

            def name_match(txt: str) -> bool:
                t = txt.lower()
                ok_last = last.lower() in t if last else True
                ok_first = first.lower() in t if first else True
                return ok_last and ok_first

            picked = False
            # cap how many candidates we inspect for speed
            for i in range(min(n, 40)):
                txt = _clean(candidate_nodes.nth(i).inner_text(timeout=1200))
                if not txt:
                    continue
                if name_match(txt):
                    print(f"[UT] clicking candidate in frame: '{txt[:80]}'")
                    # Some sites open detail in a popup window; handle both cases
                    with page.expect_popup() as maybe_popup:
                        try:
                            candidate_nodes.nth(i).click(timeout=2500)
                        except Exception:
                            pass
                    newp = None
                    try:
                        newp = maybe_popup.value
                        print("[UT] detail opened in popup.")
                    except Exception:
                        print("[UT] detail opened in same frame/tab.")
                    if newp:
                        # In popup, parse on new page
                        detail = newp
                    else:
                        # Sometimes detail loads in place within the same frame
                        detail = container
                    picked = True
                    # 7) parse detail (try within popup first, else within frame)
                    lic_no = _value_for(detail, r"License Number|License #|License No")
                    status = _value_for(detail, r"Status")
                    issue_date = _parse_date(_value_for(detail, r"Issue Date|Original Date"))
                    expiry_date = _parse_date(_value_for(detail, r"Expiration|Expiry|Expires"))

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
                    break

            if not picked:
                print("[UT] no matching row with both name tokens inside frame — returning empty.")
                return []

            return results

        except PWTimeout:
            print("[UT] timeout during interaction — returning empty.")
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
