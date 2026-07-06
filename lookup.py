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
    - License ID: compact identifier containing a digit or * -> searchCat=LicenseId
    - Anything else -> searchCat=Name
    """
    q = query.strip()

    # UBI: 9 digits, possibly spaced as "999 999 999"
    digits_only = re.sub(r"[\s\-]", "", q)
    if re.fullmatch(r"\d{9}", digits_only):
        return "Ubi", digits_only

    # Alphabetic terms are business names; WA license IDs include a digit or *.
    normalized = q.upper()
    if (
        re.fullmatch(r"[A-Z0-9*]{6,15}", normalized)
        and re.search(r"[0-9*]", normalized)
    ):
        return "LicenseId", normalized

    return "Name", q


def _lni_session() -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _warmup_session(opener: urllib.request.OpenerDirector) -> None:
    """Prime the ASP.NET session (3 round-trips, one-time cost).

    The L&I site requires an ASP.NET_SessionId cookie before search calls
    return results. This establishes it. A warmed opener can be reused for
    as many _do_search() calls as needed without repeating this setup.
    """
    headers = {"User-Agent": UA}
    req = urllib.request.Request(f"{VERIFY_BASE}/default.aspx", headers=headers)
    with opener.open(req, timeout=15) as r:
        r.read()

    warmup_url = f"{VERIFY_BASE}/Results.aspx#init"
    req2 = urllib.request.Request(warmup_url, headers=headers)
    with opener.open(req2, timeout=15) as r:
        r.read()

    req3 = urllib.request.Request(
        f"{VERIFY_BASE}/SessionHandler.aspx",
        data=json.dumps({"hash": warmup_url}).encode(),
        headers={
            **headers,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": warmup_url,
        },
    )
    with opener.open(req3, timeout=15) as r:
        r.read()


def _do_search(opener: urllib.request.OpenerDirector, search_cat: str, search_text: str, page_size: int = 25) -> dict:
    """Run one search against an already-warmed session opener."""
    headers = {"User-Agent": UA}
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
    req = urllib.request.Request(
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
    with opener.open(req, timeout=15) as r:
        return json.loads(r.read())["d"]


def _lni_search(opener: urllib.request.OpenerDirector, search_cat: str, search_text: str, page_size: int = 25) -> dict:
    """Warmup + search in one call (convenience wrapper for single lookups)."""
    _warmup_session(opener)
    return _do_search(opener, search_cat, search_text, page_size)


def _search_with_name_fallback(
    opener: urllib.request.OpenerDirector,
    search_cat: str,
    search_text: str,
    query: str,
) -> tuple[str, dict]:
    """Search once, retrying a missing license ID as a business name."""
    data = _do_search(opener, search_cat, search_text)
    if search_cat == "LicenseId" and data.get("TotalCount", 0) == 0:
        name_data = _do_search(opener, "Name", query)
        if name_data.get("TotalCount", 0) > 0:
            return "Name", name_data
    return search_cat, data


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
        _warmup_session(opener)
        search_cat, data = _search_with_name_fallback(
            opener, search_cat, search_text, query
        )
    except Exception as e:
        return {
            "action": "refine",
            "message": f"Could not reach WA L&I: {e}",
            "input": query,
            "results": [],
        }

    return _build_result(query, search_cat, data)


def batch_lookup(queries: list[str]) -> list[dict]:
    """Look up multiple contractors, sharing one session across all searches.

    Saves 3 round-trips per query after the first. Reads from a list;
    CLI --batch mode reads from stdin. Each result includes the input query.
    """
    normalized_queries = [query.strip() for query in queries]
    results_by_index = {}
    valid_queries = []

    for index, query in enumerate(normalized_queries):
        if not query or len(query) < 2:
            results_by_index[index] = {
                "action": "reject",
                "input": query,
                "message": "Query must be at least 2 characters.",
                "results": [],
            }
        else:
            valid_queries.append((index, query))

    if not valid_queries:
        return [results_by_index[index] for index in range(len(queries))]

    try:
        opener = _lni_session()
        _warmup_session(opener)
    except Exception as e:
        for index, query in valid_queries:
            results_by_index[index] = {
                "action": "refine",
                "input": query,
                "message": f"Could not reach WA L&I: {e}",
                "results": [],
            }
        return [results_by_index[index] for index in range(len(queries))]

    for index, query in valid_queries:
        search_cat, search_text = _detect_input_type(query)
        try:
            search_cat, data = _search_with_name_fallback(
                opener, search_cat, search_text, query
            )
        except Exception as e:
            results_by_index[index] = {
                "action": "refine",
                "input": query,
                "message": f"Search failed: {e}",
                "results": [],
            }
            continue
        results_by_index[index] = _build_result(query, search_cat, data)

    return [results_by_index[index] for index in range(len(queries))]


def _build_result(query: str, search_cat: str, data: dict) -> dict:
    """Build the lookup() result dict from raw API data (shared by single + batch paths)."""
    total = data.get("TotalCount", 0)
    rows = data.get("SearchResult", [])

    if total == 0:
        return {"action": "none", "message": f"No WA contractor/license found for '{query}'.", "input": query, "search_type": search_cat, "results": []}

    results = [_format_result(r) for r in rows]

    if search_cat in ("LicenseId", "Ubi") and total == 1:
        r = results[0]
        return {"action": "found", "message": f"{r['business_name']} — {r['contractor_type']} — {r['status']}", "input": query, "search_type": search_cat, "total_found": total, "results": results}

    if search_cat == "Name":
        q_upper = query.upper()
        exact = [r for r in results if r["business_name"].upper() == q_upper]
        if exact and len(exact) == 1:
            r = exact[0]
            return {"action": "found", "message": f"{r['business_name']} — {r['contractor_type']} — {r['status']}", "input": query, "search_type": search_cat, "total_found": total, "results": exact}

    showing = min(len(results), 25)
    return {"action": "pick", "message": f"Found {total} contractors matching '{query}'. Showing {showing}. Refine with a more specific name or use license_id.", "input": query, "search_type": search_cat, "total_found": total, "results": results}


EXIT_CODES = {"found": 0, "pick": 1, "none": 1, "refine": 1, "reject": 2}


def _batch_exit_code(results: list[dict]) -> int:
    """Return the highest-severity exit code across batch results."""
    return max(
        (EXIT_CODES.get(result.get("action"), 1) for result in results),
        default=0,
    )


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


def _usage():
    print("Usage: lookup.py [--pipe] [--batch] [--schema] <name|license_id|ubi>")
    print('  Human:  lookup.py "Acme Plumbing"')
    print('  Agent:  lookup.py --pipe "Acme Plumbing"')
    print('  Batch:  printf "Acme Plumbing\\nMORTESL763NR\\n" | lookup.py --batch')
    print('  Schema: lookup.py --schema')
    print("")
    print("Actions:  found (exit 0) | pick (exit 1) | none (exit 1) | reject (exit 2)")


def main():
    args = sys.argv[1:]
    pipe_mode = "--pipe" in args
    schema_mode = "--schema" in args
    batch_mode = "--batch" in args
    help_mode = "-h" in args or "--help" in args
    args = [a for a in args
            if a not in ("--pipe", "--schema", "--batch", "-h", "--help")]

    if help_mode:
        _usage()
        sys.exit(0)

    if schema_mode:
        print(json.dumps(TOOL_SCHEMA, indent=2))
        sys.exit(0)

    if batch_mode:
        queries = [line.rstrip("\n") for line in sys.stdin]
        results = batch_lookup(queries)
        for r in results:
            print(json.dumps(r, separators=(",", ":")))
        sys.exit(_batch_exit_code(results))

    if not args:
        _usage()
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
