BEGIN;
CREATE TABLE IF NOT EXISTS platform_data_retention_policies (
 tenant_id text NOT NULL, resource_type text NOT NULL,
 retain_days integer NOT NULL CHECK (retain_days > 0),
 archive_after_days integer CHECK (archive_after_days > 0 AND archive_after_days <= retain_days),
 legal_hold boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,resource_type)
);
CREATE TABLE IF NOT EXISTS platform_data_tombstones (
 tenant_id text NOT NULL, resource_type text NOT NULL, resource_id text NOT NULL,
 request_id uuid NOT NULL DEFAULT gen_random_uuid(), requested_by text NOT NULL,
 reason text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,resource_type,resource_id), UNIQUE (tenant_id,request_id)
);
CREATE TABLE IF NOT EXISTS platform_data_deletion_requests (
 tenant_id text NOT NULL, request_id uuid NOT NULL, resource_type text NOT NULL,
 resource_id text NOT NULL, requested_by text NOT NULL, reason text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','propagated','failed')),
 created_at timestamptz NOT NULL DEFAULT now(), propagated_at timestamptz,
 PRIMARY KEY (tenant_id,request_id),
 FOREIGN KEY (tenant_id,request_id) REFERENCES platform_data_tombstones(tenant_id,request_id)
);
CREATE TABLE IF NOT EXISTS platform_data_lifecycle_propagation (
 tenant_id text NOT NULL, request_id uuid NOT NULL, sink text NOT NULL
 CHECK (sink IN ('search_index','cache','object_storage')),
 status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','dead')),
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
 lease_owner text, lease_expires_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,request_id,sink),
 FOREIGN KEY (tenant_id,request_id) REFERENCES platform_data_deletion_requests(tenant_id,request_id)
);
CREATE INDEX IF NOT EXISTS idx_data_lifecycle_claim
 ON platform_data_lifecycle_propagation(status,lease_expires_at,created_at);
CREATE TABLE IF NOT EXISTS platform_data_exports (
 tenant_id text NOT NULL, export_id uuid NOT NULL, resource_type text NOT NULL,
 created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','running','succeeded','failed')),
 PRIMARY KEY (tenant_id,export_id)
);
COMMIT;
