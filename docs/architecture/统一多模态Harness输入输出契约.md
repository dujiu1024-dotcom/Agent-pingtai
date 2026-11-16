# 统一多模态 Harness 输入输出契约

## 目标

在 Harness 边界统一描述文本、图片、PDF、音频和视频输入/输出，同时保持资源授权在平台控制面和文件服务中。模型或 Agent 只能接收经过授权的资源引用，不能自行提交 URL、文件路径或对象存储凭据。

## 契约

- `MediaInput`：`modality`、`resource_id`、`mime_type`、`size_bytes`、`sha256`、可选 `filename`。
- `MediaOutput`：同样的元数据，`resource_id` 必须指向可信 Artifact Store 已持久化的产物。
- `HarnessRequest.media_inputs` 与 `HarnessResult.media_outputs`：承载零到多个媒体引用；文本仍使用既有 `input_text` / `output_text`。
- `HarnessCapabilities`：分别声明输入/输出模态，以及 streaming、cancellation、structured_output 能力。
- `CapabilityAwareHarnessAdapter`：在调用底层 Adapter 前检查输入模态，返回后检查输出模态；未声明的能力一律 fail-closed。

## 安全约束

1. resource_id 是不透明资源标识，不是 URL、路径或对象存储 key。
2. MIME type 必须与 modality 对应；必须提供正数 size 和 SHA-256。
3. filename 只能是 basename，禁止路径分隔符。
4. 资源下载/读取必须由宿主通过文件服务进行 tenant/resource 级授权，再交给具体 Provider Adapter。
5. 不允许通过 Harness 请求直接携带外部 URL、Bearer Token、签名 URL 或文件系统路径。

## 当前 Adapter 能力边界

DeepSeek Harness 与 Strands Agents 目前仍只声明文本输入/输出。它们收到 media_inputs 会明确拒绝，不会静默忽略。要启用图片/PDF/音频/视频，必须为具体 Provider 实现受控资源解析、格式转换/编码和大小限制，并在 HarnessCapabilities 中显式声明；未完成这些接线前，不得将其标记为支持。

## 验收

自动化测试覆盖资源引用/MIME/checksum 校验、未声明输入模态在模型调用前被拒绝、声明支持的 PDF/audio 输入可通过 capability guard、未声明视频输出被拒绝，以及当前 DeepSeek/Strands Adapter 对媒体输入 fail-closed。

真实文件解析、模型 Provider 的图片/PDF/audio/video 支持、对象存储授权和大文件/流式处理仍需在集成环境验证。
