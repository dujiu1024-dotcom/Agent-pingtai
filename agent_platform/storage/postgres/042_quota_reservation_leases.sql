-- Bound active quota reservations to a renewable lease so crashed workers cannot
-- hold concurrency capacity indefinitely. Existing active reservations receive a
-- conservative grace period from their original creation time.
BEGIN;

ALTER TABLE platform_quota_reservations
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz;

UPDATE platform_quota_reservations
   SET heartbeat_at = COALESCE(heartbeat_at, now()),
       lease_expires_at = COALESCE(lease_expires_at, now() + interval '5 minutes')
 WHERE status = 'active';

ALTER TABLE platform_quota_reservations
    ALTER COLUMN lease_expires_at SET DEFAULT (now() + interval '5 minutes');

CREATE INDEX IF NOT EXISTS idx_platform_quota_reservations_expiring
    ON platform_quota_reservations(lease_expires_at, reservation_id)
    WHERE status = 'active';

CREATE OR REPLACE FUNCTION platform_quota_heartbeat(
    p_reservation_id uuid,
    p_lease_seconds integer DEFAULT 300
) RETURNS boolean
LANGUAGE plpgsql AS $quota$
DECLARE changed integer;
BEGIN
    IF p_lease_seconds < 15 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'quota reservation lease must be between 15 and 3600 seconds';
    END IF;

    UPDATE platform_quota_reservations
       SET heartbeat_at = now(),
           lease_expires_at = now() + make_interval(secs => p_lease_seconds)
     WHERE reservation_id = p_reservation_id
       AND status = 'active'
       AND lease_expires_at > now();
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END;
$quota$;

CREATE OR REPLACE FUNCTION platform_quota_expire_stale(
    p_limit integer DEFAULT 500
) RETURNS integer
LANGUAGE plpgsql AS $quota$
DECLARE changed integer;
BEGIN
    IF p_limit < 1 OR p_limit > 10000 THEN
        RAISE EXCEPTION 'quota reservation expiry batch must be between 1 and 10000';
    END IF;

    WITH stale AS (
        SELECT reservation_id
          FROM platform_quota_reservations
         WHERE status = 'active'
           AND lease_expires_at <= now()
         ORDER BY lease_expires_at, reservation_id
         LIMIT p_limit
         FOR UPDATE SKIP LOCKED
    )
    UPDATE platform_quota_reservations r
       SET status = 'expired',
           settled_at = now()
      FROM stale
     WHERE r.reservation_id = stale.reservation_id
       AND r.status = 'active';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$quota$;

COMMIT;
