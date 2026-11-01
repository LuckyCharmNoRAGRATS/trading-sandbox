-- E1.14 DESIGN-ONLY FENCING PROPOSAL. DO NOT APPLY TO SUPABASE.
-- Test against disposable PostgreSQL only.
-- No time-based lease stealing: a stalled or uncertain broker submission
-- remains quarantined until explicit, separately audited reconciliation.
BEGIN;
ALTER TABLE public.commands
  ADD COLUMN IF NOT EXISTS fence_epoch bigint NOT NULL DEFAULT 0;
ALTER TABLE public.commands
  ADD CONSTRAINT e114_fence_nonnegative CHECK (fence_epoch >= 0);

-- Existing E1 claim RPC does not increment fence_epoch. This function is
-- therefore a *transition contract prototype*, NOT a deployed fencing system.
-- A future migration must atomically assign a monotonically increasing epoch
-- in the claim RPC and propagate it to every worker command.
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
