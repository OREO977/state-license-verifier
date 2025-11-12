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
    try:
        lab = container.locator(f"text=/{label_regex}/i").first
        sib = lab.locator("xpath=following::*[1]")
        txt = _clean(sib.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
    try:
        td = container.locator(
            "css=td:has-text(/%s/i) + td, th:has-text(/%s/i) + td" % (label_regex, label_regex)
        ).first
        txt = _clean(td.inner_text(timeout=1500))
        if txt:
            return txt
    except Exception:
        pass
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
    print("[UT] using main page as container.")
    return page

def _fill_inputs(container: Page | Frame, first: str, last: str) -> bool:
    """Try many ways to fill first/last. Return True if we filled at least one."""
    filled = False

    # 1) Obvious names
    try:
        if first and container.locator("input[name='firstName']").first.count():
            container.locator("input[name='firstName']").first.fill(first)
            print("[UT] filled by name=firstName")
            filled = True
    except Exception:
        pass
    try:
        if last and container.locator("input[name='lastName']").first.count():
            container.locator("input[name='lastName']").first.fill(last)
            print("[UT] filled by name=lastName")
            filled = True
    except Exception:
        pass

    # 2) aria-labels / placeholders
    patterns = [
        (r"^first", first),
        (r"^last", last),
        (r"^given", first),
        (r"^family", last),
        (r"first name", first),
        (r"last name", last),
    ]
    for pat, val in patterns:
        if not val:
            continue
        try:
            loc = container.locator(f"input[aria-label~='{val}']").first  # not great; skip
        except Exception:
            pass
        # aria-label regex
        try:
            loc = container.locator(f"input[aria-label~='{val}']").first  # placeholder fallback
        except Exception:
            pass
        try:
            loc = container.locator(f"input[placeholder=/{pat}/i]").first
            if loc.count():
                loc.fill(val)
                print(f"[UT] filled by placeholder /{pat}/i")
                filled = True
        except Exception:
            pass
        try:
            loc = container.locator(f"input[aria-label=/{pat}/i]").first
            if loc.count():
                loc.fill(val)
                print(f"[UT] filled by aria-label /{pat}/i")
                filled = True
        except Exception:
            pass

    # 3) label[for] → input#id
    try:
        labels = container.locator("label").all()
        for lab in labels:
            text = _clean(lab.inner_text())
            if not text:
                continue
            target = _clean(lab.get_attribute("for") or "")
            if not target:
                continue
            want = None
            if re.search(r"\bfirst\b", text, re.I) and first:
                want = first
            if re.search(r"\blast\b", text, re.I) and last:
                want = last
            if want:
                sel = f"input#{target}"
                if container.locator(sel).count():
                    container.locator(sel).fill(want)
                    print(f"[UT] filled via label[{text}] → {sel}")
                    filled = True
    except Exception:
        pass

    # 4) final fallback: fill *visible* text inputs with last name
    # (skip date pickers / hidden / disabled)
    if not filled and last:
        try:
            text_inputs = container.locator("input[type='text']").all()
            for el in text_inputs[:4]:  # don’t go wild
                # skip if looks like date or empty width
                placeholder = (el.get_attribute("placeholder") or "").lower()
                if "date" in placeholder or "mm" in placeholder:
                    continue
                try:
                    el.fill(last)
                    print("[UT] filled generic text input with last name")
                    filled = True
                    break
                except Exception:
                    pass
        except Exception:
            pass

    return filled

def verify_ut(full_name: str) -> List[Dict]:
    first, last = _tokens(full_name)
    print(f"[UT] lookup start → first='{first}' last='{last}'")

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

            try:
                page.get_by_role("button", name=re.compile("(accept|agree|close)", re.I)).first.click(timeout=1500)
                print("[UT] cookie/ack (shell) closed.")
            except Exception:
                pass

            container = _find_search_frame(page)

            try:
                container.get_by_role("link", name=re.compile("Name\\s*Search", re.I)).first.click(timeout=2500)
                print("[UT] Name Search tab selected.")
            except Exception:
                print("[UT] Name Search tab not clickable (likely already active).")

            # Wait for any text input to exist
            try:
                container.locator("input[type='text']").first.wait_for(timeout=4000)
            except Exception:
                print("[UT] no text inputs visible yet, continuing anyway.")

            # Try hard to fill inputs
            filled = _fill_inputs(container, first, last)
            if not filled:
                print("[UT] WARNING: no inputs got filled — search may return nothing.")

            # Try to set profession if present (best-effort)
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

            # Submit and retry a couple times in case the first never binds
            def submit_once():
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

            did_submit = submit_once()
            container.wait_for_timeout(1200)

            # Small retry loop to find results
            records_found = False
            for attempt in range(3):
                candidates = container.locator("a, td, div, span").filter(has_text=re.compile(last, re.I))
                n = candidates.count()
                print(f"[UT] result candidates (frame) attempt {attempt+1}: {n}")
                if n > 0:
                    # pick the first that also contains first name (if provided)
                    picked = False
                    for i in range(min(n, 40)):
                        txt = _clean(candidates.nth(i).inner_text(timeout=1200))
                        if not txt:
                            continue
                        ok_last = last.lower() in txt.lower() if last else True
                        ok_first = first.lower() in txt.lower() if first else True
                        if ok_last and ok_first:
                            print(f"[UT] clicking candidate: '{txt[:80]}'")
                            with page.expect_popup() as maybe_popup:
                                try:
                                    candidates.nth(i).click(timeout=2500)
                                except Exception:
                                    pass
                            newp = None
                            try:
                                newp = maybe_popup.value
                                print("[UT] detail opened in popup.")
                            except Exception:
                                print("[UT] detail opened in same frame/tab.")
                            detail = newp if newp else container

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
                            records_found = True
                            picked = True
                            break
                    if picked:
                        break
                # no candidates yet — wait a bit and try once more
                container.wait_for_timeout(1200)
                if attempt == 0 and not did_submit:
                    submit_once()

            if not records_found:
                print("[UT] no matching rows after retries — returning empty.")
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
