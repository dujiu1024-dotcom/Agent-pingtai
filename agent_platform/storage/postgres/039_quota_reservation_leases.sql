-- Bounded leases for quota reservations so crashed workers cannot hold slots forever.
BEGIN;

ALTER TABLE platform_quota_reservations
    ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;

-- Give already-running work a fresh grace period during rollout. New reservations
-- receive a 15-minute lease; workers must renew before the deadline.
UPDATE platform_quota_reservations
   SET heartbeat_at = COALESCE(heartbeat_at, now()),
       expires_at = COALESCE(expires_at, now() + interval '15 minutes')
 WHERE status = 'active';

ALTER TABLE platform_quota_reservations
    ALTER COLUMN heartbeat_at SET DEFAULT now(),
    ALTER COLUMN expires_at SET DEFAULT (now() + interval '15 minutes');

CREATE INDEX IF NOT EXISTS idx_platform_quota_reservations_expiring
    ON platform_quota_reservations(expires_at, reservation_id)
    WHERE status = 'active';

CREATE OR REPLACE FUNCTION platform_quota_renew(
    p_reservation_id uuid,
    p_lease_seconds integer DEFAULT 900
) RETURNS boolean
LANGUAGE plpgsql AS $quota$
DECLARE
    changed integer;
BEGIN
    IF p_lease_seconds < 30 OR p_lease_seconds > 3600 THEN
        RAISE EXCEPTION 'reservation lease must be between 30 and 3600 seconds';
    END IF;

    UPDATE platform_quota_reservations
       SET heartbeat_at = clock_timestamp(),
           expires_at = clock_timestamp() + make_interval(secs => p_lease_seconds)
     WHERE reservation_id = p_reservation_id
       AND status = 'active'
       AND expires_at > clock_timestamp();
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed = 1;
END;
$quota$;

CREATE OR REPLACE FUNCTION platform_quota_expire(
    p_batch_size integer DEFAULT 500
) RETURNS integer
LANGUAGE plpgsql AS $quota$
DECLARE
    changed integer;
BEGIN
    IF p_batch_size < 1 OR p_batch_size > 10000 THEN
        RAISE EXCEPTION 'expiration batch size must be between 1 and 10000';
    END IF;

    WITH expired AS (
        SELECT reservation_id
          FROM platform_quota_reservations
         WHERE status = 'active'
           AND expires_at <= clock_timestamp()
         ORDER BY expires_at, reservation_id
         FOR UPDATE SKIP LOCKED
         LIMIT p_batch_size
    )
    UPDATE platform_quota_reservations AS reservation
       SET status = 'expired',
           settled_at = clock_timestamp()
      FROM expired
     WHERE reservation.reservation_id = expired.reservation_id
       AND reservation.status = 'active';
    GET DIAGNOSTICS changed = ROW_COUNT;
    RETURN changed;
END;
$quota$;

COMMIT;
