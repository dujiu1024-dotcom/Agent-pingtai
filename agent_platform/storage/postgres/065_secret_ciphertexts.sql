BEGIN;

-- Ciphertext-only envelope storage. The KMS encrypted data key is inside envelope.
CREATE TABLE IF NOT EXISTS platform_secret_ciphertexts (
    secret_ref text NOT NULL,
    version integer NOT NULL CHECK (version > 0),
    envelope bytea NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (secret_ref, version),
    FOREIGN KEY (secret_ref, version)
      REFERENCES platform_secret_versions(secret_ref, version)
      ON DELETE CASCADE
);

COMMIT;
