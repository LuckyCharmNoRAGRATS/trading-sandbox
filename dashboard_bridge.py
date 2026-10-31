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