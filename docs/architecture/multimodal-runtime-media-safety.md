# Multimodal Runtime Media Reference Safety

Harness media inputs and outputs carry opaque platform resource identifiers, not caller-provided URLs or filesystem paths. The Runtime contract validates modality, MIME type, positive byte size and SHA-256 before adapter dispatch.

The reference boundary also rejects:
- control characters and whitespace in opaque resource IDs;
- CR/LF and other ASCII control characters anywhere in MIME metadata;
- path separators, dot-segment filenames, and control characters in filenames.

These checks prevent header/log injection and path confusion when a trusted host later resolves or emits media metadata. They do not replace resource-level authorization: the host must resolve each opaque ID under the authenticated tenant and Run, verify the digest against stored bytes, and enforce the adapter's declared modality/MIME/size policy. Unsupported modalities must fail before Provider invocation.
