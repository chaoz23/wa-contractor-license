# WA Contractor License Verifier

Look up Washington State contractor registration and license status via the WA L&I Verify portal.

Accepts a business name, license ID, or 9-digit UBI number. Returns whether the contractor is actively licensed, what type of work they're licensed for, and any safety or contractor violations on record.

```bash
python3 lookup.py "Acme Plumbing"              # by business name
python3 lookup.py "MORTESL763NR"               # by license ID
python3 lookup.py "605417027"                  # by UBI number
python3 lookup.py --pipe "Acme Plumbing"       # agent pipeline mode
python3 lookup.py --schema                     # print tool definition
```

## What you get

```json
{
  "action": "found",
  "message": "MORTENSON SIGNS, LLC — Construction Contractor — Active",
  "total_found": 1,
  "results": [
    {
      "license_id": "MORTESL763NR",
      "business_name": "MORTENSON SIGNS, LLC",
      "contractor_type": "Construction Contractor",
      "contractor_group": "Construction Contractor",
      "status": "Active",
      "city": "MOUNT VERNON",
      "state": "WA",
      "ubi": "605417027",
      "violations": [],
      "detail_url": "https://secure.lni.wa.gov/verify/Detail.aspx?..."
    }
  ]
}
```

| Field | Description |
|---|---|
| `action` | `found` (match), `pick` (multiple — narrow query), `none` (no match), `refine` (network issue), `reject` (bad input) |
| `results` | Up to 25 matching contractors |
| `total_found` | Total records in L&I database (may exceed 25) |

Per result: `license_id`, `business_name`, `contractor_type`, `contractor_group`, `status` (Active/Expired/Inactive), `city`, `state`, `ubi`, `violations` (array: `"safety"`, `"contractor"`), `detail_url`.

## Search behavior

| Input | `action` | Notes |
|---|---|---|
| `"Acme Plumbing"` | `found` | Exact name match in top 25 results |
| `"plumbing"` | `pick` | Many matches — narrow with city or full name |
| `"MORTESL763NR"` | `found` | License ID exact match |
| `"605417027"` | `found` | UBI exact match |
| `"XYZ Fake Co"` | `none` | Not in L&I database |
| `"a"` | `reject` | Too short |

## Exit codes

| Code | Action | Meaning |
|---|---|---|
| 0 | `found` | License verified — `results[]` is populated |
| 1 | `pick` / `none` / `refine` | Multiple matches or no match |
| 2 | `reject` | Bad input — do not retry without changing query |

Batch mode emits every NDJSON result, then exits with the highest-severity code
across the batch. For example, any `reject` makes the batch exit `2`; otherwise
any `pick`, `none`, or `refine` makes it exit `1`. An empty batch exits `0`.

## Coverage

All WA-licensed contractor types:
- Construction contractors (general, specialty)
- Electrical contractors and trainees
- Plumbers
- HVAC
- Roofers, insulators, elevator contractors, and more

## For agents

`tool.json` at the repo root contains the full tool definition in Anthropic/OpenAI tool-call format:

```bash
python3 lookup.py --schema
```

**Pipeline pattern:**

```bash
# Verify a contractor before hiring — check for active license and violations
python3 lookup.py --pipe "Acme Plumbing" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d['action'] == 'found':
    r = d['results'][0]
    print('Active:', r['status'] == 'Active')
    print('Violations:', r['violations'])
    print('License:', r['license_id'])
elif d['action'] == 'pick':
    print('Multiple matches:', d['total_found'], '— refine query')
else:
    print('Not found')
"
```

**Related tools in this series:**
- [`king-county-permit-status`](https://github.com/chaoz23/king-county-permit-status) — look up permit history by address, parcel, or permit number
- [`king-county-address-to-parcel-number`](https://github.com/chaoz23/king-county-address-to-parcel-number) — resolve an address to its parcel number

## Requirements

- Python 3.10+ (stdlib only, no dependencies)
- Network access to `secure.lni.wa.gov`

## License

MIT
