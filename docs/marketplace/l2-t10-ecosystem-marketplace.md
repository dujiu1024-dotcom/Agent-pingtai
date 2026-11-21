# L2-T10 开放生态市场与治理运营

## Delivered
- 发布提交时执行签名、来源证明、安全扫描和评测四项硬门禁。
- 资产版本使用不可变的 tenant/asset/version 唯一键；版本清单保留独立版本。
- 审核必须由非发布者完成，记录审核人、结果和原因。
- 试运行仅对审核通过版本开放；适配器调用既有共享 OCI 沙箱，必须传入 network disabled、no host mounts、no credential injection，并返回隔离证据。
- 发布门禁要求审核、签名、来源证明、扫描、评测、隔离试运行全部通过。
- 评分仅允许已发布版本、真实安装验证通过的用户；发布者不能自评，同一安装每版本只能评分一次。
- 支持撤回；市场查询只返回当前租户内已发布资产。

## Reuse boundaries
本模块只维护市场发布元数据和审批状态，不重建 Extension Registry、包签名、SBOM/来源验证、Skill/Extension 包扫描或 OCI Sandbox。通过 adapter 调用现有可信发布者注册、供应链准入、评测和 `OciSandboxRunner`/`ExtensionOciRuntime`。市场数据库使用宿主持久化 Store；随附 InMemory store 仅用于单元测试。

## API
- GET /api/platform/v1/marketplace/assets
- POST /api/platform/v1/marketplace/submissions
- POST /assets/{id}/versions/{version}/review
- POST /assets/{id}/versions/{version}/trial
- POST /assets/{id}/versions/{version}/publish
- POST /assets/{id}/versions/{version}/withdraw
- POST /assets/{id}/versions/{version}/ratings
- GET /assets/{id}/versions

## Verification
`python -m pytest tests/test_marketplace_governance.py -q`

Real-machine/production sandbox validation is skipped as requested. The host must inject production signature, provenance, scanner, evaluator, sandbox, installation-verifier and durable-store adapters; absent adapters fail closed by not mounting the API.
