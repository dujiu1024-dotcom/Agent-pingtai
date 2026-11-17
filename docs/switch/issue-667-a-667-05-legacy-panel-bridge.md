# A-667-05 legacy panel compatibility bridge

The new Workbench now exposes a clearly named **旧版功能兼容入口** linking to
`/app/?legacy=1`. The gateway's legacy fallback (introduced by S-34-03) serves
the archived legacy console through that explicit route.

## Why this is a bridge, not feature parity

PR #702 introduced Workbench, Batch, Replay and Observability surfaces, but
explicitly left agency-only business, content and roundtable panels without
equivalent platform APIs. A single legacy-console entry makes those workflows
reachable while their platform-native replacements are implemented; it does
not claim those panels have been migrated or re-authorized through new APIs.

**兼容入口不等于功能迁移完成**. The legacy console remains subject to its
existing deployment and API dependencies. Unknown or unavailable legacy APIs
must continue to fail closed; this link does not add a backend bypass.

## Contract

- The entry is visible in the new Workbench navigation.
- It points only to the explicit `/app/?legacy=1` fallback route.
- The label identifies it as legacy compatibility, not a native Workbench page.
- A focused unittest protects the navigation contract and the incomplete
  acceptance status from being misrepresented.

## Remaining A-667-05 work

The business, content and roundtable panels still need platform-native pages
and tenant-authorized API adapters, with feature-by-feature parity tests.
Production gateway/browser verification of the legacy fallback is also
required before the bridge can be considered operationally validated.

A-667-05 继续保持未验收. This PR provides an incremental access bridge only;
it does not close Issue #667 or mark the acceptance checkbox complete.
