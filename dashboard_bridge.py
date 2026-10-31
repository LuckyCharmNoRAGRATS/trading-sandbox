import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

try:
    from supabase import create_client, Client
except ImportError:
    Client = Any

logger = logging.getLogger(__name__)

class DashboardBridge:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        self.client: Optional[Client] = None
        
        if url and key:
            try:
                self.client = create_client(url, key)
            except Exception as e:
                logger.error(f"Failed to initialize Supabase client: {e}")
        else:
            logger.warning("Supabase credentials missing. Dashboard bridge operating in offline mode.")

    def set_kv(self, key: str, value: dict) -> bool:
        if not self.client: 
            return False
        try:
            self.client.table("kv").upsert({"key": key, "data": value}).execute()
            return True
        except Exception as e:
            logger.error(f"Supabase KV write failed: {e}")
            return False

    def claim_next_trade_command(self):
        """Atomic RPC claim. Missing migration, database or RPC errors fail closed."""
        from uuid import uuid4
        if not self.client:
            raise RuntimeError("Command database unavailable")
        token = str(uuid4())
        response = self.client.rpc(
            "claim_next_trade_command", {"p_claim_token": token}
        ).execute()
        records = response.data or []
        if len(records) > 1:
            raise RuntimeError("Claim RPC returned multiple commands")
        if not records:
            return None
        cmd = records[0]
        if cmd.get("claim_token") != token or cmd.get("status") != "claimed":
            raise RuntimeError("Invalid command claim ownership")
        return cmd

    def transition_claimed(self, command, expected_status, new_status, message="", broker_id=None):
        """Compare-and-swap state with owner token; no zero-row success."""
        if not self.client or not command.get("claim_token"):
            raise RuntimeError("Cannot update without database and claim token")
        payload = {"status": new_status, "result": message}
        if new_status == "submitting":
            payload["submitted_at"] = datetime.now(timezone.utc).isoformat()
        if broker_id is not None:
            payload["broker_order_id"] = str(broker_id)
        rows = (
            self.client.table("commands").update(payload)
            .eq("id", command["id"])
            .eq("claim_token", command["claim_token"])
            .eq("status", expected_status)
            .select("id").execute()
        ).data
        if rows is None or len(rows) != 1 or rows[0].get("id") != command["id"]:
            raise RuntimeError("Command state transition not confirmed")
        return True

    def unresolved_trade_commands(self):
        """Read only; any unresolved trade blocks further risk-increasing orders."""
        if not self.client:
            raise RuntimeError("Command database unavailable")
        response = (
            self.client.table("commands").select("id,status,client_order_id")
            .eq("cmd", "submit_order")
            .in_("status", ["claimed", "submitting", "uncertain", "acknowledged"])
            .limit(1).execute()
        )
        if response.data is None:
            raise RuntimeError("Cannot confirm recovery state")
        return response.data

    def get_pending_commands(self) -> List[Dict[str, Any]]:
        if not self.client: 
            return []
        try:
            res = self.client.table("commands").select("*").eq("status", "pending").execute()
            return res.data if res.data else []
        except Exception as e:
            logger.error(f"Supabase command fetch failed: {e}")
            return []
            
    def mark_command_processed(self, command_id: str, status: str = "completed", message: str = "") -> bool:
        if not self.client: 
            return False
        try:
            self.client.table("commands").update({"status": status, "result": message}).eq("id", command_id).execute()
            return True
        except Exception as e:
            logger.error(f"Supabase command update failed: {e}")
            return False

# Global singleton for easy import across modules
bridge = DashboardBridge()

def set_kv(key: str, value: dict):
    return bridge.set_kv(key, value)
    
class DashboardLogHandler(logging.Handler):
    def emit(self, record):
        if not bridge.client: 
            return
        try:
            log_entry = self.format(record)
            bridge.client.table("logs").insert({
                "level": record.levelname,
                "msg": log_entry,
                "ts": datetime.now(timezone.utc).isoformat()
            }).execute()
        except Exception:
            pass