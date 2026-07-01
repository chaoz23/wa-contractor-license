# WA Contractor License Verifier

Look up a WA contractor's license status, type, and violations:

```bash
python3 lookup.py "Business Name"      # by name (returns exact match or list)
python3 lookup.py "MORTESL763NR"       # by license ID (exact)
python3 lookup.py "605417027"          # by UBI number (exact)
```

Read the `action` field:
- `found` → contractor found, check `results[0].status` (Active/Expired/Inactive) and `results[0].violations`
- `pick` → multiple matches; the input name was ambiguous — show the list and ask for clarification
- `none` → contractor not in L&I database, possibly not licensed in WA
- `refine` → network issue, suggest retrying

## API Notes

Uses `secure.lni.wa.gov/verify/Controller.aspx/Search` (ASP.NET Web Method).

**Critical:** The POST body must include `searchDto[searchCat] = searchText` as a named field (e.g., `"Name": "Acme Plumbing"` alongside `"searchCat": "Name", "searchText": "Acme Plumbing"`). Without this field, all searches return 0 results.

**Session setup required:**
1. GET `default.aspx` (primes cookies)
2. GET `Results.aspx#<encoded-searchDto>` (establishes context)
3. POST `SessionHandler.aspx` with `{"hash": results_url}` (sets ASP.NET session)
4. POST `Controller.aspx/Search` with `{"dtoSrch": searchDto}` (actual search)

**IrlStatusCode** meanings: `A` = Active, `E` = Expired. `Status = "View Details"` also = Active.
