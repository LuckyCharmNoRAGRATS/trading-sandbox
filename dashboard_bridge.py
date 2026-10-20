"""
dashboard_bridge.py: Real-time 2-way bridge between ~/trading-sandbox (Alpaca) and Web/Mobile Dashboard.
"""
import os, queue, logging, threading, time, datetime as dt
import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(override=True)

_sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
_hook = os.getenv("DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_STATUS_WEBHOOK")
_q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
_now = lambda: dt.datetime.now(dt.timezone.utc).isoformat()


def _worker():
    while True:
        table, row = _q.get()
        try:
            if table == "_upsert_kv":
                _sb.table("kv").upsert(row).execute()
            elif table == "_upsert_signal":
                _sb.table("signals").upsert(row).execute()
            else:
                _sb.table(table).insert(row).execute()
        except Exception as e:
            print("bridge write failed:", e)
        finally:
            _q.task_done()

threading.Thread(target=_worker, daemon=True).start()


def flush():
    _q.join()

def log(level, msg, source="bot"):
    _q.put(("logs", {"level": level, "msg": str(msg)[:2000], "source": source}))

def set_kv(key, data):
    _q.put(("_upsert_kv", {"key": key, "data": data, "updated_at": _now()}))

def set_signal(ticker, strategy, status, metrics, enabled=None):
    row = {"ticker": ticker, "strategy": strategy, "status": status, "metrics": metrics, "updated_at": _now()}
    if enabled is not None:
        row["enabled"] = enabled
    _q.put(("_upsert_signal", row))

def push_result(kind, name, metrics):
    _q.put(("results", {"kind": kind, "name": name, "metrics": metrics}))

def push_trade_row(trade_dict: dict):
    """Pushes a live Alpaca fill directly into the Excel Execution Spreadsheet on the website."""
    _q.put(("results", {"kind": "TRADE_LEDGER", "name": trade_dict.get("id", "ORD"), "metrics": trade_dict}))

def record_command(cmd, source, result, status="done"):
    _q.put(("commands", {"cmd": cmd, "source": source, "result": str(result)[:1000], "status": status}))

def notify(msg):
    if _hook:
        try:
            requests.post(_hook, json={"content": msg[:1900]}, timeout=8)
        except Exception:
            pass


class DashboardLogHandler(logging.Handler):
    _skip = ("httpx", "httpcore", "hpack", "supabase", "postgrest", "realtime", "urllib3")

    def emit(self, record):
        if record.name.startswith(self._skip):
            return
        log(record.levelname, self.format(record))


def serve_commands(handlers, every=1.5):
    """Polls commands sent from your Phone or Laptop browser and executes them against Alpaca."""
    def loop():
        while True:
            try:
                rows = _sb.table("commands").select("*").eq("status", "pending").order("id").execute().data
                for r in rows:
                    _sb.table("commands").update({"status": "running"}).eq("id", r["id"]).execute()
                    try:
                        res, status = handlers[r["cmd"]](r.get("args") or {}), "done"
                    except KeyError:
                        res, status = f"No handler for {r['cmd']}", "error"
                    except Exception as e:
                        res, status = str(e), "error"
                    _sb.table("commands").update({"status": status, "result": str(res or "ok")[:1000]}).eq("id", r["id"]).execute()
                    log("INFO" if status == "done" else "ERROR", f"[Web->Alpaca] {r['cmd']} ({status}): {res}", "oms")
                    notify(f"📱 **Web/Mobile Command** `{r['cmd']}` → `{status}`: {res or ''}")
            except Exception as e:
                print("command loop error:", e)
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()