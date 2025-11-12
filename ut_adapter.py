# ut_adapter.py — Utah lookup using the discovered 'fullName' field + 'containing' radio.

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page, Frame
import datetime as dt
import re
from typing import List, Dict, Optional

UT_URL = "https://secure.utah.gov/llv/search/index.html#"

def _clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()

def _tokens(name: str):
    parts = [p for p in re.split(r"\s+", name.strip()) if p]
    first = " ".join(parts[:-1]) if len(parts) > 1 else ""
    last = parts[-1] if parts else ""
    return first, last

def _parse_date(s: str):
    s = _clean(s)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except Exception:
            pass
    return None

def _value_for(container: Page | Frame, label_regex: str) -> str:
    # label → following sibling
    try:
        lab = container.locator(f"text=/{label_regex}/i").first
        sib = lab.locator("xpath=following::*[1]")
        txt = _clean(sib.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    # table cell pattern
    try:
        td = container.locator(
            "css=td:has-text(/%s/i) + td, th:has-text(/%s/i) + td" % (label_regex, label_regex)
        ).first
        txt = _clean(td.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    # definition list
    try:
        dd = container.locator("css=dt:has-text(/%s/i) + dd" % label_regex).first
        txt = _clean(dd.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    return ""

def _find_frame_with_search(page: Page) -> Frame | Page:
    frames = page.frames
    print(f"[UT] frames detected: {len(frames)}")
    for f in frames:
        try:
            if f.locator("input[name='fullName']").first.count():
                print("[UT] selected frame by fullName input.")
                return f
        except Exception:
            pass
        try:
            if f.locator("text=/Name\\s*Search/i").first.count():
                print("[UT] selected frame by 'Name Search' text.")
                return f
        except Exception:
            pass
    print("[UT] using main page as container.")
    return page

def _maybe_pick_physician(container: Page | Frame):
    """Best-effort: tick a profession that mentions Physician (optional)."""
    try:
        # labels tied to checkboxes look like: <label for="itemX">PHYSICIAN ...</label>
        lab = container.locator("label", has_text=re.compile("Physician", re.I)).first
        if lab.count():
            lab.click(timeout=1500)
            print("[UT] profession label clicked (Physician…).")
            return True
    except Exception:
        pass
    return False

def _search(container: Page | Frame):
    try:
        container.get_by_role("button", name=re.compile("Search", re.I)).first.click(timeout=2500)
        print("[UT] Search clicked.")
        return True
    except Exception:
        try:
            container.keyboard.press("Enter")
            print("[UT] Enter pressed to submit.")
            return True
        except Exception:
            return False

def verify_ut(full_name: str) -> List[Dict]:
    first, last = _tokens(full_name)
    print(f"[UT] lookup start → full='{full_name}' last='{last}'")

    results: List[Dict] = []
    ACTION_TIMEOUT = 12_000

    with sync_playwright() as p:
        browser = None
        ctx = None
        page = None
        try:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()
            page = ctx.new_page()
            page.set_default_timeout(ACTION_TIMEOUT)

            print("[UT] goto search page…")
            page.goto(UT_URL, wait_until="domcontentloaded", timeout=ACTION_TIMEOUT)

            # Close any shell-level banners
            try:
                page.get_by_role("button", name=re.compile("(accept|agree|close)", re.I)).first.click(timeout=1500)
                print("[UT] cookie/ack (shell) closed.")
            except Exception:
                pass

            container = _find_frame_with_search(page)

            # Make sure Name Search tab is active (best effort)
            try:
                container.get_by_role("link", name=re.compile("Name\\s*Search", re.I)).first.click(timeout=2500)
                print("[UT] Name Search tab selected.")
            except Exception:
                print("[UT] Name Search likely already active.")

            # Wait for the fullName input and fill it
            try:
                full_input = container.locator("input[name='fullName'], input#fullName").first
                full_input.wait_for(timeout=4000)
                full_input.fill(full_name)
                print("[UT] filled fullName.")
            except Exception as e:
                print(f"[UT] ERROR: could not fill fullName: {e}")
                return []

            # Set "containing" mode (radio)
            try:
                # Try by id first
                radio = container.locator("input[name='startsWith']#containing").first
                if radio.count():
                    radio.check()
                    print("[UT] radio 'containing' checked by id.")
                else:
                    # Fallback: label text
                    container.get_by_label(re.compile("CONTAINING", re.I)).check(timeout=1500)
                    print("[UT] radio 'containing' checked by label.")
            except Exception:
                print("[UT] could not set 'containing' radio (continuing).")

            # Optional: tick a Physician-ish profession to narrow
            _maybe_pick_physician(container)

            # Submit
            did = _search(container)
            container.wait_for_timeout(1200)

            # Look for results inside the same frame
            # Strategy: rows/links containing the last name
            for attempt in range(3):
                # Try table rows first
                rows = container.locator("tr").filter(has_text=re.compile(last, re.I))
                rn = rows.count()
                links = container.locator("a").filter(has_text=re.compile(last, re.I))
                ln = links.count()
                print(f"[UT] results attempt {attempt+1}: rows={rn} links={ln}")

                target_clicked = False
                # Prefer clicking a link inside a matching row (more precise)
                if rn > 0:
                    for i in range(min(rn, 40)):
                        row = rows.nth(i)
                        rtxt = _clean(row.inner_text(timeout=1200))
                        if last.lower() in rtxt.lower() and (not first or first.lower() in rtxt.lower()):
                            # Click a link inside the row, else click the row
                            try:
                                row.locator("a").first.click(timeout=2000)
                                print(f"[UT] clicked link inside row: '{rtxt[:80]}'")
                            except Exception:
                                row.click(timeout=2000)
                                print(f"[UT] clicked row: '{rtxt[:80]}'")
                            target_clicked = True
                            break

                # If no rows clicked, try any matching link
                if not target_clicked and ln > 0:
                    for i in range(min(ln, 40)):
                        ltxt = _clean(links.nth(i).inner_text(timeout=1200))
                        if last.lower() in ltxt.lower() and (not first or first.lower() in ltxt.lower()):
                            with page.expect_popup() as pop:
                                try:
                                    links.nth(i).click(timeout=2000)
                                except Exception:
                                    pass
                            try:
                                detail = pop.value
                                print("[UT] detail opened in popup.")
                            except Exception:
                                detail = container
                                print("[UT] detail opened in same frame/tab.")
                            container = detail  # parse from detail page/frame
                            target_clicked = True
                            break

                if target_clicked:
                    break

                # Dump a small snippet for debugging and retry
                try:
                    snippet = _clean(container.locator("body").inner_text(timeout=1200))[:300]
                    print(f"[UT] RESULT SNIPPET: {snippet}")
                except Exception:
                    pass
                container.wait_for_timeout(1200)
                if attempt == 0 and not did:
                    _search(container)

            # If we never navigated to detail, try parsing in-place (some sites expand inline)
            lic_no = _value_for(container, r"License Number|License #|License No")
            status = _value_for(container, r"Status")
            issue_date = _parse_date(_value_for(container, r"Issue Date|Original Date"))
            expiry_date = _parse_date(_value_for(container, r"Expiration|Expiry|Expires"))

            if not any([lic_no, status, issue_date, expiry_date]):
                print("[UT] no detail fields found — returning empty.")
                return []

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
