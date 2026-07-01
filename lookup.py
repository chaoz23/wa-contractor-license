#!/usr/bin/env python3
"""WA L&I Contractor License Verifier.

Look up Washington State contractor and tradesperson license status via
the WA L&I Verify portal (secure.lni.wa.gov/verify/).

Inputs accepted:
  - Business name (any string 2+ chars)
  - License ID (e.g. MORTESL763NR, ALDERC*862J2)
  - UBI number (9 digits, e.g. 605417027)

Two modes, same JSON output:
  Human:  python3 lookup.py "Acme Plumbing"
  Agent:  python3 lookup.py --pipe "Acme Plumbing"

Every response includes:
  action  — what a pipeline should do: "found", "pick", "none", "reject"
  message — what a human should read

Exit codes:
  0 = found (action=found), exact or near-exact match, license_id is valid
  1 = pick / none — multiple ambiguous matches or no match found
  2 = bad input (action=reject), do not retry without changing input
"""

import json
import re
import sys
import urllib.parse
import urllib.request
import http.cookiejar

VERIFY_BASE = "https://secure.lni.wa.gov/verify"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _detect_input_type(query: str) -> tuple[str, str]:
    """Return (search_cat, normalized_text).

    - UBI: 9 digits (with or without spaces/hyphens) -> searchCat=Ubi
    - License ID: letters+digits+optional* -> searchCat=LicenseId
    - Anything else -> searchCat=Name
    """
    q = query.strip()

    # UBI: 9 digits, possibly spaced as "999 999 999"
    digits_only = re.sub(r"[\s\-]", "", q)
    if re.fullmatch(r"\d{9}", digits_only):
        return "Ubi", digits_only

    # License ID: typically 10-12 alphanumeric chars, may contain *
    if re.fullmatch(r"[A-Z0-9*]{6,15}", q.upper()):
        return "LicenseId", q.upper()

    return "Name", q


def _lni_session() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _lni_search(opener, search_cat: str, search_text: str, page_size: int = 25) -> dict:
    """Core search. Returns the raw 'd' dict from Controller.aspx/Search."""
    headers = {"User-Agent": UA}

    # Step 1: load default.aspx to prime cookies
    req = urllib.request.Request(f"{VERIFY_BASE}/default.aspx", headers=headers)
    with opener.open(req, timeout=15) as r:
        r.read()

    # Step 2: build the searchDto as default.js would
    search_dto = {
        "pageNumber": 0,
        "SearchType": 2,
        "SortColumn": "Rank",
        "SortOrder": "desc",
        "pageSize": page_size,
        "ContractorTypeFilter": [],
        "SessionID": "",
        "SAW": "",
        "searchCat": search_cat,
        "searchText": search_text,
        search_cat: search_text,  # critical: named field matching searchCat
        "firstSearch": 1,
    }

    results_url = f"{VERIFY_BASE}/Results.aspx#{urllib.parse.quote(json.dumps(search_dto))}"

    # Step 3: load Results.aspx (establishes session context)
    req2 = urllib.request.Request(results_url, headers=headers)
    with opener.open(req2, timeout=15) as r:
        r.read()

    # Step 4: POST SessionHandler.aspx
    req3 = urllib.request.Request(
        f"{VERIFY_BASE}/SessionHandler.aspx",
        data=json.dumps({"hash": results_url}).encode(),
        headers={
            **headers,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": results_url,
        },
    )
    with opener.open(req3, timeout=15) as r:
        r.read()

    # Step 5: POST Controller.aspx/Search
    req4 = urllib.request.Request(
        f"{VERIFY_BASE}/Controller.aspx/Search",
        data=json.dumps({"dtoSrch": search_dto}).encode(),
        headers={
            **headers,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": results_url,
        },
    )
    with opener.open(req4, timeout=15) as r:
        return json.loads(r.read())["d"]


def _normalize_status(row: dict) -> str:
    """Map IrlStatusCode + Status to a human-readable status string."""
    code = row.get("IrlStatusCode", "") or ""
    status = row.get("Status", "") or ""
    if code == "A" or status == "View Details":
        return "Active"
    if code in ("E", "X"):
        return "Expired"
    if status.lower() == "inactive":
        return "Inactive"
    return status or "Unknown"


def _format_result(row: dict) -> dict:
    violations = []
    if row.get("HasSafetyViolation"):
        violations.append("safety")
    if row.get("HasContractorViolation"):
        violations.append("contractor")

    return {
        "license_id": row.get("LicenseId", ""),
        "business_name": row.get("BusinessName", ""),
        "contractor_type": row.get("ContractorType", ""),
        "contractor_group": row.get("ContractorGroup", ""),
        "status": _normalize_status(row),
        "city": row.get("City", ""),
        "state": row.get("State", ""),
        "ubi": row.get("Ubi", "") or None,
        "violations": violations,
        "detail_url": (
            f"https://secure.lni.wa.gov/verify/Detail.aspx"
            f"?LicenseType={urllib.parse.quote(str(row.get('ContractorGroup') or ''))}"
            f"&LicenseNumber={urllib.parse.quote(str(row.get('LicenseId') or ''))}"
        ),
    }


def lookup(query: str) -> dict:
    query = query.strip()
    if not query or len(query) < 2:
        return {
            "action": "reject",
            "message": "Query must be at least 2 characters.",
            "input": query,
            "results": [],
        }

    search_cat, search_text = _detect_input_type(query)

    opener = _lni_session()
    try:
        data = _lni_search(opener, search_cat, search_text)
    except Exception as e:
        return {
            "action": "refine",
            "message": f"Could not reach WA L&I: {e}",
            "input": query,
            "results": [],
        }

    total = data.get("TotalCount", 0)
    rows = data.get("SearchResult", [])

    if total == 0:
        return {
            "action": "none",
            "message": f"No WA contractor/license found for '{query}'.",
            "input": query,
            "search_type": search_cat,
            "results": [],
        }

    results = [_format_result(r) for r in rows]

    # Exact match: LicenseId/Ubi search with exactly 1 result, or Name exact match
    if search_cat in ("LicenseId", "Ubi") and total == 1:
        r = results[0]
        return {
            "action": "found",
            "message": f"{r['business_name']} — {r['contractor_type']} — {r['status']}",
            "input": query,
            "search_type": search_cat,
            "total_found": total,
            "results": results,
        }

    # Name search: check for strong match
    if search_cat == "Name":
        q_upper = query.upper()
        exact = [r for r in results if r["business_name"].upper() == q_upper]
        if exact and len(exact) == 1:
            r = exact[0]
            return {
                "action": "found",
                "message": f"{r['business_name']} — {r['contractor_type']} — {r['status']}",
                "input": query,
                "search_type": search_cat,
                "total_found": total,
                "results": exact,
            }

    # Multiple matches
    showing = min(len(results), 25)
    return {
        "action": "pick",
        "message": (
            f"Found {total} contractors matching '{query}'. "
            f"Showing {showing}. Refine with a more specific name or use license_id."
        ),
        "input": query,
        "search_type": search_cat,
        "total_found": total,
        "results": results,
    }


EXIT_CODES = {"found": 0, "pick": 1, "none": 1, "refine": 1, "reject": 2}

TOOL_SCHEMA = {
    "name": "wa_contractor_license",
    "description": (
        "Look up Washington State contractor registration and license status via WA L&I. "
        "Accepts a business name, license ID (e.g. MORTESL763NR), or 9-digit UBI number. "
        "Returns action=found (match returned), action=pick (multiple matches — narrow the query), "
        "action=none (no match), or action=reject (bad input). "
        "Use this to verify that a contractor hired for work in WA has an active license, "
        "check for safety or contractor violations, or look up license details before hiring. "
        "Covers construction contractors, electricians, plumbers, HVAC, roofers, and all other "
        "WA-licensed contractor types."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Business name, license ID, or UBI. "
                    "Examples: 'Acme Plumbing', 'MORTESL763NR', '605417027'. "
                    "For name searches, partial names work but may return many results. "
                    "License ID searches return exact matches only."
                ),
            }
        },
        "required": ["query"],
    },
    "output_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["found", "pick", "none", "refine", "reject"],
                "description": (
                    "found — license(s) returned, results[] is populated; "
                    "pick — multiple matches, present to user or narrow query; "
                    "none — no license found for this query; "
                    "refine — network issue, retry; "
                    "reject — bad input, don't retry"
                ),
            },
            "total_found": {
                "type": ["integer", "null"],
                "description": "Total matching records in L&I database (may exceed results[] length).",
            },
            "results": {
                "type": "array",
                "description": "Up to 25 matching contractors.",
                "items": {
                    "type": "object",
                    "properties": {
                        "license_id": {"type": "string", "description": "WA L&I license number"},
                        "business_name": {"type": "string"},
                        "contractor_type": {"type": "string", "description": "e.g. Construction Contractor, Electrician"},
                        "contractor_group": {"type": "string", "description": "e.g. Construction Contractor, Electrical"},
                        "status": {"type": "string", "description": "Active | Expired | Inactive"},
                        "city": {"type": "string"},
                        "state": {"type": "string"},
                        "ubi": {"type": ["string", "null"], "description": "WA Unified Business Identifier (9 digits)"},
                        "violations": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of violation types: 'safety' and/or 'contractor'",
                        },
                        "detail_url": {"type": "string", "description": "Link to full license detail on WA L&I"},
                    },
                },
            },
            "message": {"type": "string"},
        },
        "required": ["action", "message", "results"],
    },
    "invocation": {
        "command": "python3 lookup.py --pipe \"{query}\"",
        "exit_codes": {
            "0": "action=found — license verified, results[] populated",
            "1": "action=pick/none/refine — multiple matches or no match",
            "2": "action=reject — bad input, do not retry",
        },
    },
}


def main():
    args = sys.argv[1:]
    pipe_mode = "--pipe" in args
    schema_mode = "--schema" in args
    args = [a for a in args if a not in ("--pipe", "--schema")]

    if schema_mode:
        print(json.dumps(TOOL_SCHEMA, indent=2))
        sys.exit(0)

    if not args:
        print("Usage: lookup.py [--pipe] [--schema] <name|license_id|ubi>")
        print('  Human:  lookup.py "Acme Plumbing"')
        print('  Agent:  lookup.py --pipe "Acme Plumbing"')
        print('  Schema: lookup.py --schema')
        print("")
        print("Actions:  found (exit 0) | pick (exit 1) | none (exit 1) | reject (exit 2)")
        sys.exit(2)

    query = " ".join(args)
    result = lookup(query)

    if pipe_mode:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print(json.dumps(result, indent=2))

    sys.exit(EXIT_CODES.get(result["action"], 1))


if __name__ == "__main__":
    main()
