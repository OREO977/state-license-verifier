import datetime as dt

def verify_ut(full_name: str):
    """
    Temporary fake record so you can prove the pipeline works
    (no scraping yet — we’ll replace this with the real lookup next).
    """
    if "gregory osmond" in full_name.lower():
        return [{
            "full_name": full_name,
            "state": "UT",
            "license_number": "1234567-1205",
            "status": "Active",
            "issue_date": dt.date(2015, 5, 1),
            "expiry_date": dt.date(2026, 1, 31),
            "source_uri": "https://secure.utah.gov/llv/search/index.html#",
            "last_verified_at": dt.datetime.utcnow(),
        }]
    return []
