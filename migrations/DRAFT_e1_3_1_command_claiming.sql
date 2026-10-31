-- E1.3.1 DESIGN DRAFT ONLY. DO NOT APPLY UNTIL REVIEWED.
-- Maintenance prerequisite: STOP all legacy execution workers and inspect pending commands.
BEGIN;
ALTER TABLE public.commands
 ADD COLUMN request_id uuid,
 ADD COLUMN claim_token uuid,
 ADD COLUMN claimed_at timestamptz,
 ADD COLUMN client_order_id text,
 ADD COLUMN broker_order_id text,
 ADD COLUMN submitted_at timestamptz;

CREATE UNIQUE INDEX commands_request_id_uq ON public.commands(request_id)
 WHERE request_id IS NOT NULL;
CREATE UNIQUE INDEX commands_client_order_id_uq ON public.commands(client_order_id)
 WHERE client_order_id IS NOT NULL;
CREATE INDEX commands_pending_trade_idx ON public.commands(created_at, id)
 WHERE status = 'pending' AND cmd = 'submit_order';

CREATE FUNCTION public.claim_next_trade_command(p_claim_token uuid)
RETURNS SETOF public.commands
LANGUAGE plpgsql SECURITY INVOKER SET search_path = ''
AS $$
BEGIN
 IF p_claim_token IS NULL THEN RAISE EXCEPTION 'claim token required'; END IF;
 -- Fail closed on ANY unresolved prior submission. No lease-based automatic retries.
 IF EXISTS (SELECT 1 FROM public.commands
   WHERE cmd = 'submit_order'
   AND status IN ('claimed','submitting','uncertain')) THEN
   RETURN;
 END IF;
 RETURN QUERY
 WITH next_command AS (
   SELECT c.id FROM public.commands c
   WHERE c.status = 'pending' AND c.cmd = 'submit_order'
   ORDER BY c.created_at, c.id
   FOR UPDATE SKIP LOCKED LIMIT 1
 )
 UPDATE public.commands c
 SET status='claimed', claim_token=p_claim_token, claimed_at=clock_timestamp(),
     client_order_id=COALESCE(c.client_order_id, 'e1-' || c.id::text)
 FROM next_command
 WHERE c.id=next_command.id
 RETURNING c.*;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_next_trade_command(uuid)
 FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_next_trade_command(uuid) TO service_role;
COMMIT;

-- ROLLBACK (never run before orders reconciled, worker stopped, and metadata exported):
-- BEGIN;
-- DO $$ BEGIN
--   IF EXISTS (SELECT 1 FROM public.commands
--      WHERE status IN ('claimed','submitting','uncertain','acknowledged'))
--   THEN RAISE EXCEPTION 'Unreconciled commands: rollback blocked'; END IF;
-- END $$;
-- DROP FUNCTION public.claim_next_trade_command(uuid);
-- DROP INDEX public.commands_pending_trade_idx;
-- DROP INDEX public.commands_client_order_id_uq;
-- DROP INDEX public.commands_request_id_uq;
-- ALTER TABLE public.commands DROP COLUMN submitted_at, DROP COLUMN broker_order_id,
-- DROP COLUMN client_order_id, DROP COLUMN claimed_at, DROP COLUMN claim_token, DROP COLUMN request_id;
-- COMMIT;
