# ut_adapter.py — Utah lookup: fullName + PHYSICIAN checkbox + result <a> click.

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

def _find_search_frame(page: Page) -> Frame | Page:
    frames = page.frames
    print(f"[UT] frames detected: {len(frames)}")
    for f in frames:
        try:
            if f.locator("input[name='fullName'], input#fullName").first.count():
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

def _submit_search(container: Page | Frame) -> bool:
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

def _click_result_link(page: Page, first: str, last: str) -> Optional[Frame | Page]:
    """Search all frames for an <a> whose text contains the doctor's name, click it, and return the frame to parse."""
    print("[UT] searching for result link...")
    for f in page.frames:
        try:
            links = f.locator("a").filter(has_text=re.compile(last, re.I))
            count = links.count()
            print(f"[UT] frame {f.name or '<no-name>'}: candidate links with last name = {count}")
            for i in range(min(count, 40)):
                txt = _clean(links.nth(i).inner_text(timeout=1200))
                if not txt:
                    continue
                if last.lower() in txt.lower() and (not first or first.lower() in txt.lower()):
                    print(f"[UT] clicking result link: '{txt}'")
                    links.nth(i).click(timeout=3000)
                    return f
        except Exception as e:
            print(f"[UT] error scanning frame for links: {e}")
    print("[UT] no result link found for provider.")
    return None

def verify_ut(full_name: str) -> List[Dict]:
    first, last = _tokens(full_name)
    print(f"[UT] lookup start → full='{full_name}' last='{last}'")

    results: List[Dict] = []
    ACTION_TIMEOUT = 15_000

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

            # close any shell-level banners
            try:
                page.get_by_role("button", name=re.compile("(accept|agree|close)", re.I)).first.click(timeout=1500)
                print("[UT] cookie/ack (shell) closed.")
            except Exception:
                pass

            container = _find_search_frame(page)

            # ensure Name Search tab is active
            try:
                container.get_by_role("link", name=re.compile("Name\\s*Search", re.I)).first.click(timeout=2500)
                print("[UT] Name Search tab selected.")
            except Exception:
                print("[UT] Name Search likely already active.")

            # ---- FILL THE NAME ----
            try:
                full_input = container.locator("input[name='fullName'], input#fullName").first
                full_input.fill(full_name)
                print("[UT] filled fullName.")
            except Exception as e:
                print(f"[UT] ERROR filling fullName: {e}")
                return []

            # set "containing" radio if possible
            try:
                radio = container.locator("input[name='startsWith']#containing").first
                if radio.count():
                    radio.check()
                    print("[UT] 'containing' radio checked by id.")
                else:
                    container.get_by_label(re.compile("CONTAINING", re.I)).check(timeout=1500)
                    print("[UT] 'containing' radio checked by label.")
            except Exception:
                print("[UT] could not set 'containing' radio (continuing).")

            # click the PHYSICIAN checkbox (item273)
            try:
                phys = container.locator("input.licenseType#item273").first
                if phys.count():
                    phys.check()
                    print("[UT] PHYSICIAN checkbox (item273) checked.")
                else:
                    print("[UT] PHYSICIAN checkbox item273 not found.")
            except Exception as e:
                print(f"[UT] could not click PHYSICIAN checkbox: {e}")

            # submit search
            _submit_search(container)
            container.wait_for_timeout(1500)

            # ---- CLICK THE RESULT LINK ----
            result_frame = _click_result_link(page, first, last)
            if result_frame is None:
                # log snippet for debugging
                try:
                    snippet = _clean(container.locator("body").inner_text(timeout=1200))[:300]
                    print(f"[UT] RESULT SNIPPET (no link): {snippet}")
                except Exception:
                    pass
                return []

            # allow detail to load
            result_frame.wait_for_timeout(1500)

            # ---- PARSE DETAIL ----
            detail_container: Frame | Page = result_frame

            lic_no = _value_for(detail_container, r"License Number|License #|License No")
            status = _value_for(detail_container, r"Status")
            issue_date = _parse_date(_value_for(detail_container, r"Issue Date|Original Date"))
            expiry_date = _parse_date(_value_for(detail_container, r"Expiration|Expiry|Expires"))

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
