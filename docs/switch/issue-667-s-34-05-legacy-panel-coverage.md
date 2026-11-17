# S-34-05 legacy panel coverage map

This page is the migration index for the legacy `console.html` feature panels.
It is intentionally explicit about which destinations are native platform UI
and which still use the legacy compatibility page.

| Legacy capability | New workbench destination | Current behavior |
|---|---|---|
| Task board | Task Center | Native new UI entry |
| Run execution | Task Center | Native new UI entry; Runtime remains server-side |
| Run reports / artifacts | Artifact Delivery | Native new UI entry |
| Event stream | Observability | Native new UI entry; event/audit data remains API-backed |
| Operations dashboard | Legacy `ops` panel | Compatibility deep link |
| System health self-check | Legacy `health` panel | Compatibility deep link |
| Business: clients, quotes, contracts, invoices, payments | Legacy `business` panel | Compatibility deep link |
| Content calendar and publishing metrics | Legacy `content` panel | Compatibility deep link |
| Multi-role roundtable | Legacy `roundtable` panel | Compatibility deep link |

## Navigation contract

The authenticated workbench exposes **旧版功能入口**. Native destinations navigate
inside the new workbench. Compatibility destinations open `/app/?legacy=1&panel=<name>`;
the legacy console reads an allowlisted `panel` value and opens the corresponding
existing overlay after its initial state load. Unknown panel names do not trigger
any overlay.

## Acceptance boundary

This change makes every inventoried legacy feature panel discoverable from the new
workbench and preserves direct access during migration. It does **not** claim that
business, content, roundtable, operations, or self-check have been rewritten as
new platform APIs or migrated authorization/data models. Those domains remain on
the legacy backend and must not be marked fully migrated until their server-side
contracts and permission checks are moved to the platform API.

## Verification

Run:

```bash
python -m pytest tests/test_issue_667_s_34_05_legacy_panel_coverage.py tests/test_issue_667_s_34_02_workbench_pages.py -q
```

The focused test verifies the workbench entry, native destinations, allowlisted
legacy panel deep links, and the explicit migration boundary. Browser verification
and authenticated end-to-end operation against a deployed legacy backend remain
deployment checks.
