-- E1.14 DESIGN-ONLY FENCING PROPOSAL. DO NOT APPLY TO SUPABASE.
-- Test against disposable PostgreSQL only.
-- No time-based lease stealing: a stalled or uncertain broker submission
-- remains quarantined until explicit, separately audited reconciliation.
BEGIN;
ALTER TABLE public.commands
  ADD COLUMN IF NOT EXISTS fence_epoch bigint NOT NULL DEFAULT 0;
ALTER TABLE public.commands
  ADD CONSTRAINT e114_fence_nonnegative CHECK (fence_epoch >= 0);

-- Prototype claim path: global transaction advisory lock serializes the
-- unresolved check and assignment. Epoch increments on first claim; no
-- automatic reclaim or reset is allowed. This is NOT wired to Python worker.
CREATE OR REPLACE FUNCTION public.e114_claim_next_trade_command(p_claim_token uuid)
RETURNS SETOF public.commands
LANGUAGE plpgsql SECURITY INVOKER SET search_path = ''
AS $
BEGIN
 IF p_claim_token IS NULL THEN RAISE EXCEPTION 'Claim token required'; END IF;
 IF NOT pg_catalog.pg_try_advisory_xact_lock(31031, 1) THEN RETURN; END IF;
 IF EXISTS (SELECT 1 FROM public.commands
   WHERE cmd='submit_order'
     AND status IN ('claimed','submitting','uncertain','acknowledged'))
 THEN RETURN; END IF;
 RETURN QUERY
 WITH next_command AS (
   SELECT c.id FROM public.commands c
   WHERE c.status='pending' AND c.cmd='submit_order'
   ORDER BY c.created_at,c.id FOR UPDATE SKIP LOCKED LIMIT 1
 )
 UPDATE public.commands c
 SET status='claimed', claim_token=p_claim_token,
     claimed_at=clock_timestamp(),
     fence_epoch=c.fence_epoch+1,
     client_order_id=COALESCE(c.client_order_id,'e1-'||c.id::text)
 FROM next_command WHERE c.id=next_command.id RETURNING c.*;
END;
$;
REVOKE ALL ON FUNCTION public.e114_claim_next_trade_command(uuid)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.e114_claim_next_trade_command(uuid)
  TO service_role;

CREATE OR REPLACE FUNCTION public.e114_fenced_transition(
  p_id bigint,
  p_claim_token uuid,
  p_fence_epoch bigint,
  p_expected text,
  p_next text
) RETURNS boolean
LANGUAGE plpgsql SECURITY INVOKER SET search_path = ''
AS $$
DECLARE changed bigint;
BEGIN
  IF p_id IS NULL OR p_claim_token IS NULL OR p_fence_epoch IS NULL
     OR p_expected IS NULL OR p_next IS NULL THEN
    RAISE EXCEPTION 'Missing fencing inputs';
  END IF;
  IF NOT (
      (p_expected = 'claimed' AND p_next IN ('submitting','failed'))
      OR (p_expected = 'submitting' AND p_next IN ('acknowledged','uncertain'))
      OR (p_expected = 'uncertain' AND p_next = 'uncertain')
    ) THEN
    RAISE EXCEPTION 'Unsafe command transition % -> %', p_expected, p_next;
  END IF;
  UPDATE public.commands c
     SET status = p_next
   WHERE c.id = p_id
     AND c.cmd = 'submit_order'
     AND c.claim_token = p_claim_token
     AND c.fence_epoch = p_fence_epoch
     AND c.status = p_expected;
  GET DIAGNOSTICS changed = ROW_COUNT;
  RETURN changed = 1;
END;
$$;
REVOKE ALL ON FUNCTION public.e114_fenced_transition(bigint,uuid,bigint,text,text)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.e114_fenced_transition(bigint,uuid,bigint,text,text)
  TO service_role;
COMMIT;
