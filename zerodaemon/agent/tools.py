"""LangChain @tool definitions — the agent's real-world capabilities."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Tool: check_ip_owner
# ---------------------------------------------------------------------------

def check_ip_owner(ip_address: str) -> str:
    """
    Find out who owns an IP address (ISP, cloud provider, organisation).
    Returns the ASN description and registered org name.
    """
    try:
        from ipwhois import IPWhois
        result = IPWhois(ip_address).lookup_rdap(depth=1)
        asn_desc = result.get("asn_description") or "Unknown ASN"
        network = result.get("network", {})
        org = network.get("name") or network.get("remarks") or ""
        return json.dumps({
            "ip": ip_address,
            "asn": result.get("asn"),
            "asn_description": asn_desc,
            "org": org,
            "country": result.get("asn_country_code"),
        })
    except Exception as exc:
        return json.dumps({"ip": ip_address, "error": str(exc)})


# ---------------------------------------------------------------------------
# Tool: scan_services
# ---------------------------------------------------------------------------

def scan_services(ip_address: str, ports: str = "top-100") -> str:
    """
    Run a fast Nmap service scan against an IP address to discover open ports
    and software versions. Results are persisted to the local database.
    Always run this before querying for CVEs.
    """
    try:
        import nmap
        nm = nmap.PortScanner()
        args = "-sV -T4 --top-ports 100" if ports == "top-100" else f"-sV -T4 -p {ports}"
        nm.scan(ip_address, arguments=args)

        scan_id = str(uuid.uuid4())
        raw = nm[ip_address].all_protocols() if ip_address in nm.all_hosts() else {}

        open_ports = []
        for proto in (raw if isinstance(raw, list) else []):
            port_data = nm[ip_address].get(proto, {})
            for port, info in port_data.items():
                if info.get("state") == "open":
                    open_ports.append({
                        "port": port,
                        "proto": proto,
                        "service": info.get("name"),
                        "product": info.get("product"),
                        "version": info.get("version"),
                    })

        summary = f"Found {len(open_ports)} open port(s) on {ip_address}"
        raw_json = json.dumps(nm[ip_address] if ip_address in nm.all_hosts() else {})

        # Persist to local database (sync — tools run in executor threads)
        from zerodaemon.core.config import get_settings
        db_path = get_settings().db_path
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO scans (id, ts, target, scan_type, raw_json, summary) VALUES (?,?,?,?,?,?)",
            (scan_id, _now_iso(), ip_address, "service", raw_json, summary),
        )
        conn.commit()
        conn.close()

        return json.dumps({
            "scan_id": scan_id,
            "target": ip_address,
            "open_ports": open_ports,
            "summary": summary,
        })
    except Exception as exc:
        return json.dumps({"target": ip_address, "error": str(exc)})


# ---------------------------------------------------------------------------
# Tool: search_threat_intel
# ---------------------------------------------------------------------------

def search_threat_intel(query: str) -> str:
    """
    Search the live internet for recent CVEs, exploits, or threat intelligence
    related to a given IP, software version, or vulnerability keyword.
    Use this after finding open ports/services to check for known issues.
    """
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query + " CVE exploit 2025 2026", max_results=4))
        formatted = [
            {"title": r["title"], "url": r["href"], "body": r["body"][:300]}
            for r in results
        ]
        return json.dumps({"query": query, "results": formatted})
    except Exception as exc:
        return json.dumps({"query": query, "error": str(exc)})


# ---------------------------------------------------------------------------
# Tool: query_historical_scans
# ---------------------------------------------------------------------------

def query_historical_scans(ip_address: str, limit: int = 5) -> str:
    """
    Retrieve previous scan results for an IP from the local database.
    Use this BEFORE running a live scan to detect drift (new ports, changed services).
    """
    try:
        from zerodaemon.core.config import get_settings
        db_path = get_settings().db_path
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, summary, raw_json FROM scans WHERE target=? ORDER BY ts DESC LIMIT ?",
            (ip_address, limit),
        ).fetchall()
        conn.close()
        records = [dict(r) for r in rows]
        if not records:
            return json.dumps({"ip": ip_address, "history": [], "message": "No previous scans found"})
        return json.dumps({"ip": ip_address, "history": records})
    except Exception as exc:
        return json.dumps({"ip": ip_address, "error": str(exc)})


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def get_tools():
    """Return the list of LangChain tool callables for the agent graph."""
    from langchain_core.tools import tool as lc_tool
    return [
        lc_tool(check_ip_owner),
        lc_tool(scan_services),
        lc_tool(search_threat_intel),
        lc_tool(query_historical_scans),
    ]
