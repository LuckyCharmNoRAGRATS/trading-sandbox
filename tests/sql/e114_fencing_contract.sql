\set ON_ERROR_STOP on
-- Disposable CI database only. Requires E1.3.1 fixture already installed.
\ir ../../migrations/DRAFT_e1_14_fencing.sql

DO $$
BEGIN
 IF has_function_privilege('anon',
   'public.e114_fenced_transition(bigint,uuid,bigint,text,text)','EXECUTE')
 OR has_function_privilege('authenticated',
   'public.e114_fenced_transition(bigint,uuid,bigint,text,text)','EXECUTE')
 THEN RAISE EXCEPTION 'Untrusted roles can transition'; END IF;
 IF NOT has_function_privilege('service_role',
   'public.e114_fenced_transition(bigint,uuid,bigint,text,text)','EXECUTE')
 THEN RAISE EXCEPTION 'Service role missing transition privilege'; END IF;
END $$;

INSERT INTO public.commands(cmd,status,claim_token,client_order_id,fence_epoch)
VALUES ('submit_order','claimed',
        '00000000-0000-4000-8000-000000000141'::uuid,
        'e1-141', 7);

SET ROLE service_role;
DO $$
DECLARE cid bigint; allowed boolean; status_now text;
BEGIN
 SELECT id INTO cid FROM public.commands
 WHERE client_order_id='e1-141';
 SELECT public.e114_fenced_transition(
   cid,'00000000-0000-4000-8000-000000000141',6,'claimed','submitting')
 INTO allowed;
 IF allowed THEN RAISE EXCEPTION 'Stale epoch was accepted'; END IF;
 SELECT public.e114_fenced_transition(
   cid,'00000000-0000-4000-8000-000000000142',7,'claimed','submitting')
 INTO allowed;
 IF allowed THEN RAISE EXCEPTION 'Wrong token was accepted'; END IF;
 SELECT public.e114_fenced_transition(
   cid,'00000000-0000-4000-8000-000000000141',7,'claimed','submitting')
 INTO allowed;
 IF NOT allowed THEN RAISE EXCEPTION 'Valid transition rejected'; END IF;
 SELECT public.e114_fenced_transition(
   cid,'00000000-0000-4000-8000-000000000141',7,'claimed','submitting')
 INTO allowed;
 IF allowed THEN RAISE EXCEPTION 'Duplicate transition accepted'; END IF;
 SELECT public.e114_fenced_transition(
   cid,'00000000-0000-4000-8000-000000000141',7,'submitting','uncertain')
 INTO allowed;
 IF NOT allowed THEN RAISE EXCEPTION 'Quarantine transition rejected'; END IF;
 SELECT status INTO status_now FROM public.commands WHERE id=cid;
 IF status_now <> 'uncertain' THEN
   RAISE EXCEPTION 'Unexpected terminal state %',status_now;
 END IF;
END $$;
RESET ROLE;

-- Confirm unsafe re-submit from uncertain raises, not silently succeeds.
DO $$
DECLARE cid bigint;
BEGIN
 SELECT id INTO cid FROM public.commands WHERE client_order_id='e1-141';
 BEGIN
  PERFORM public.e114_fenced_transition(
    cid,'00000000-0000-4000-8000-000000000141',7,'uncertain','submitting');
  RAISE EXCEPTION 'Unsafe transition unexpectedly accepted';
 EXCEPTION WHEN raise_exception THEN
  IF SQLERRM = 'Unsafe transition unexpectedly accepted' THEN RAISE; END IF;
 END;
END $$;

-- Prototype claim must atomically set token, stable broker ID and epoch.
-- Reset only the disposable fixture; this is NOT recovery policy.
UPDATE public.commands SET status='failed' WHERE cmd='submit_order';
INSERT INTO public.commands(cmd,status,args)
VALUES ('submit_order','pending','{}'::jsonb);
SET ROLE service_role;
DO $$
DECLARE rec record; duplicate_count bigint;
BEGIN
 SELECT * INTO rec FROM public.e114_claim_next_trade_command(
  '00000000-0000-4000-8000-000000000143'::uuid);
 IF rec.id IS NULL OR rec.status <> 'claimed' OR rec.fence_epoch <> 1
    OR rec.client_order_id <> 'e1-'||rec.id::text
    OR rec.claim_token <> '00000000-0000-4000-8000-000000000143'::uuid
 THEN RAISE EXCEPTION 'Prototype claim did not establish fencing identity'; END IF;
 SELECT count(*) INTO duplicate_count FROM public.e114_claim_next_trade_command(
  '00000000-0000-4000-8000-000000000144'::uuid);
 IF duplicate_count <> 0 THEN
   RAISE EXCEPTION 'Second worker claimed during unresolved command';
 END IF;
END $$;
RESET ROLE;
