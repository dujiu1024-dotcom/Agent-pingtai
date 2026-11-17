# Runtime multimodal capability contract (C-12-06)

The Harness adapter contract uses opaque, authorized media references rather than user-supplied URLs or filesystem paths. `MediaInput` and `MediaOutput` carry modality, MIME type, byte size, SHA-256 digest and an optional safe basename.

## Declared modalities

Adapters declare independent `input_modalities` and `output_modalities` from `text`, `image`, `pdf`, `audio`, and `video`. A modality omitted from the relevant set is unsupported. The capability-aware wrapper rejects unsupported input before calling the adapter and rejects undeclared output before returning it. There is no implicit conversion or silent dropping of media.

## Media constraints

The wrapper validates MIME type and size for both inputs and outputs. Default policy:

| Modality | MIME types | Default maximum |
| --- | --- | ---: |
| image | image/jpeg, image/png, image/webp, image/gif | 20 MiB |
| pdf | application/pdf | 50 MiB |
| audio | audio/mpeg, audio/mp3, audio/wav, audio/x-wav, audio/ogg, audio/webm, audio/mp4 | 100 MiB |
| video | video/mp4, video/webm, video/quicktime | 250 MiB |

Adapters may narrow defaults with `input_media_max_bytes`, `output_media_max_bytes`, `input_media_mime_types`, and `output_media_mime_types`. Policies may only refer to modalities declared in the corresponding direction. A provider-specific codec or format outside the allowlist must be rejected or transcoded by a separately trusted preprocessing stage before creating the authorized media reference; this contract does not pretend to transcode content.

## Failure and downgrade semantics

Unsupported modality, MIME type or size raises `UnsupportedHarnessCapability`; the adapter is not invoked for invalid inputs. Output violations fail the run. The runtime never silently strips a PDF, image, audio track or video. A caller may explicitly supply a separately extracted text representation as ordinary text, but must preserve its provenance and must not describe that as native multimodal understanding.

## Security and verification

Resource IDs must remain opaque and authorized by the platform asset layer. MIME declarations do not replace content sniffing, malware scanning, tenant authorization or artifact-store checks. Tests use synthetic references only. Provider-backed multimodal quality, codec behavior and production storage integration require separate environment-specific validation and are not claimed by unit tests.
