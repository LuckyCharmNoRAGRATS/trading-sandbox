#!/usr/bin/env bash
set -euo pipefail
export PGHOST=127.0.0.1

# All operations are against CI's disposable postgres service.
psql -X -v ON_ERROR_STOP=1 -q -c "INSERT INTO public.commands(cmd,args) VALUES ('submit_order', '{\"symbol\":\"SPY\"}')" >/dev/null

psql -X -v ON_ERROR_STOP=1 -At -c "SET ROLE service_role; SELECT id FROM public.claim_next_trade_command('00000000-0000-4000-8000-000000000011'::uuid);" > /tmp/e1_claim_a &
pid_a=$!
psql -X -v ON_ERROR_STOP=1 -At -c "SET ROLE service_role; SELECT id FROM public.claim_next_trade_command('00000000-0000-4000-8000-000000000012'::uuid);" > /tmp/e1_claim_b &
pid_b=$!
wait "$pid_a"
wait "$pid_b"

count="$(psql -X -v ON_ERROR_STOP=1 -At -c "SELECT count(*) FROM public.commands WHERE status='claimed';")"
if [[ "$count" != "1" ]]; then
  echo "FAIL: expected exactly one claimed command, got $count" >&2
  exit 1
fi
echo "PASS: competing claims did not create multiple claimed orders"

# Confirm unique request identities are enforced (test transaction expected to fail).
psql -X -v ON_ERROR_STOP=1 -q -c "INSERT INTO public.commands(cmd,request_id,status) VALUES ('test','00000000-0000-4000-8000-000000000099'::uuid,'failed');"
if psql -X -v ON_ERROR_STOP=1 -q -c "INSERT INTO public.commands(cmd,request_id,status) VALUES ('test','00000000-0000-4000-8000-000000000099'::uuid,'failed');" >/dev/null 2>&1; then
  echo "FAIL: duplicate request_id accepted" >&2
  exit 1
fi
echo "PASS: duplicate request_id rejected"
