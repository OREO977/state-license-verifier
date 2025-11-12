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

def _log_inputs(container: Page | Frame):
    try:
        inputs = container.locator("input").all()
        print(f"[UT] INPUT COUNT: {len(inputs)}")
        for idx, el in enumerate(inputs[:12]):  # keep logs readable
            try:
                t = (el.get_attribute("type") or "").lower()
                nm = el.get_attribute("name")
                iid = el.get_attribute("id")
                ph = el.get_attribute("placeholder")
                ar = el.get_attribute("aria-label")
                lab_text = ""
                # try to find <label for=...>
                if iid:
                    lbl = container.locator(f"label[for='{iid}']").first
                    if lbl.count():
                        lab_text = _clean(lbl.inner_text() or "")
                print(f"[UT] INPUTS[{idx}]: type={t} name={nm} id={iid} placeholder={ph} aria={ar} label={lab_text}")
            except Exception as e:
                print(f"[UT] INPUTS[{idx}]: <error reading attrs: {e}>")
    except Exception as e:
        print(f"[UT] could not enumerate inputs: {e}")

def _fill_by_patterns(container: Page | Frame, patterns: List[tuple], value: str, tag="first/last") -> bool:
    for css in patterns:
        try:
            loc = container.locator(css).first
            if loc.count():
                loc.fill(value)
                print(f"[UT] filled {tag} via selector: {css}")
                return True
        except Exception:
            pass
    return False

def _fill_inputs(container: Page | Frame, first: str, last: str) -> bool:
    filled = False

    # Try explicit names/ids/placeholders/aria for FIRST
    if first:
        first_patterns = [
            "input[name='firstName']",
            "input[id='firstName']",
            "input[placeholder*='First' i]",
            "input[aria-label*='First' i]",
            "input[aria-label*='Given' i]",
            "input[aria-label='First Name']",
            "input[placeholder='First Name']",
        ]
        filled = _fill_by_patterns(container, first_patterns, first, "first") or filled

    # Try explicit names/ids/placeholders/aria for LAST
    if last:
        last_patterns = [
            "input[name='lastName']",
            "input[id='lastName']",
            "input[placeholder*='Last' i]",
            "input[aria-label*='Last' i]",
            "input[aria-label*='Family' i]",
            "input[aria-label='Last Name']",
            "input[placeholder='Last Name']",
        ]
        filled = _fill_by_patterns(container, last_patterns, last, "last") or filled

    # Label->for mapping (works when inputs lack helpful attributes)
    try:
        labels = container.locator("label").all()
        for lab in labels:
            txt = _clean(lab.inner_text() or "")
            if not txt:
                continue
            target = _clean(lab.get_attribute("for") or "")
            if not target:
                continue
            if first and re.search(r"\bfirst\b", txt, re.I):
                sel = f"input#{target}"
                if container.locator(sel).count():
                    container.locator(sel).fill(first)
                    print(f"[UT] filled first via label '{txt}' → {sel}")
                    filled = True
            if last and re.search(r"\blast\b", txt, re.I):
                sel = f"input#{target}"
                if container.locator(sel).count():
                    container.locator(sel).fill(last)
                    print(f"[UT] filled last via label '{txt}' → {sel}")
                    filled = True
    except Exception:
        pass

    # Final fallback: fill first two visible text inputs (avoid date pickers)
    if not filled and (first or last):
        try:
            text_inputs = []
            for el in container.locator("input[type='text']").all():
                t = (el.get_attribute("type") or "").lower()
                ph = (el.get_attribute("placeholder") or "").lower()
                ar = (el.get_attribute("aria-label") or "").lower()
                if "date" in ph or "date" in ar or "mm" in ph:
                    continue
                text_inputs.append(el)
            print(f"[UT] fallback text inputs available: {len(text_inputs)}")
            if text_inputs:
                # If we have both names, try to put first in first field, last in second
                if first and last and len(text_inputs) >= 2:
                    text_inputs[0].fill(first)
                    text_inputs[1].fill(last)
                    print("[UT] fallback filled first & last into first two text inputs.")
                    filled = True
                else:
                    # Otherwise, fill last name at least
                    text_inputs[0].fill(last or first)
                    print("[UT] fallback filled single text input.")
                    filled = True
        except Exception as e:
            print(f"[UT] fallback fill error: {e}")

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

            # Log inputs we see (so we can pick exact selectors next)
            _log_inputs(container)

            # Fill inputs robustly
            filled = _fill_inputs(container, first, last)
            if not filled:
                print("[UT] WARNING: no inputs filled — search likely to return nothing.")

            # Profession (best-effort)
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

            # Submit search
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
            container.wait_for_timeout(1500)

            # Try a few times to detect results
            for attempt in range(3):
                candidates = container.locator("a, td, div, span").filter(has_text=re.compile(last, re.I))
                n = candidates.count()
                print(f"[UT] result candidates (frame) attempt {attempt+1}: {n}")
                if n > 0:
                    # Choose first candidate also containing first name (if provided)
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
                            return results

                # No candidates yet — dump a little of the DOM text for hints
                try:
                    snippet = _clean(container.locator("body").inner_text(timeout=1200))[:300]
                    print(f"[UT] RESULT BODY SNIPPET: {snippet}")
                except Exception:
                    pass

                container.wait_for_timeout(1200)
                if attempt == 0 and not did_submit:
                    submit_once()

            print("[UT] no matching rows after retries — returning empty.")
            return []

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
