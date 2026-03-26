"""LangChain @tool definitions — the agent's real-world capabilities."""

from __future__ import annotations

import json
import socket
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve(target: str) -> str:
    """Resolve a hostname to an IP address; return the target unchanged if it's already an IP."""
    try:
        socket.inet_aton(target)  # raises if not a valid IPv4 literal
        return target
    except OSError:
        pass
    try:
        return socket.getaddrinfo(target, None)[0][4][0]
    except Exception as exc:
        raise ValueError(f"Cannot resolve '{target}': {exc}") from exc


# ---------------------------------------------------------------------------
# Tool: check_ip_owner
# ---------------------------------------------------------------------------

def check_ip_owner(ip_address: str) -> str:
    """
    Find out who owns an IP address or hostname (ISP, cloud provider, organisation).
    Returns the ASN description and registered org name.
    """
    try:
        ip_address = _resolve(ip_address)
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

_PORT_PRESETS: dict[str, tuple[str, str]] = {
    "top-10":   ("--top-ports 10",   "~5–15 s"),
    "top-100":  ("--top-ports 100",  "~15–45 s"),
    "top-1000": ("--top-ports 1000", "~1–3 min"),
    "full":     ("-p 1-65535",       "~10–30 min"),
}


def scan_services(ip_address: str, ports: str = "top-100") -> str:
    """
    Run an Nmap service scan (-sV -T4 -Pn) to discover open ports and software versions.
    Results are persisted to the local database.

    ports presets and estimated times (vary by network/host responsiveness):
      top-10   — most common 10 ports,   ~5–15 s    (quick sanity check)
      top-100  — most common 100 ports,  ~15–45 s   (default, good balance)
      top-1000 — most common 1000 ports, ~1–3 min   (thorough)
      full     — all 65535 ports,        ~10–30 min (exhaustive, slow)
    You may also pass a custom nmap port expression, e.g. "22,80,443" or "1-1024".
    Always run this before querying for CVEs.
    """
    try:
        ip_address = _resolve(ip_address)
        import shutil
        if not shutil.which("nmap"):
            return json.dumps({
                "target": ip_address,
                "error": "nmap is not installed. Run: sudo apt install nmap  (or set ZERODAEMON_AUTO_INSTALL_DEPS=true)",
            })
        import nmap
        nm = nmap.PortScanner()
        if ports in _PORT_PRESETS:
            port_arg, _ = _PORT_PRESETS[ports]
            args = f"-sV -T4 -Pn {port_arg}"
        else:
            args = f"-sV -T4 -Pn -p {ports}"
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

        # Index into RAG vector store
        from zerodaemon.agent import rag
        rag.add_scan(scan_id, ip_address, summary, raw_json)

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
        result_json = json.dumps({"query": query, "results": formatted})

        # Index into RAG vector store
        from zerodaemon.agent import rag
        rag.add_threat_intel(query, result_json)

        return result_json
    except Exception as exc:
        return json.dumps({"query": query, "error": str(exc)})


# ---------------------------------------------------------------------------
# Tool: query_historical_scans
# ---------------------------------------------------------------------------

def query_historical_scans(ip_address: str, limit: int = 5) -> str:
    """
    Retrieve previous scan results for an IP or hostname from the local database.
    Use this BEFORE running a live scan to detect drift (new ports, changed services).
    """
    try:
        ip_address = _resolve(ip_address)
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
# Tool: search_knowledge_base
# ---------------------------------------------------------------------------

def search_knowledge_base(query: str) -> str:
    """
    Semantic search over the local knowledge base of past scan results and threat
    intelligence reports.  Use this to answer questions like "what services have
    I seen on the 10.0.0.x subnet?", "any CVEs related to nginx in my history?",
    or "what did I find last time I scanned this host?".
    Returns the most relevant stored documents.
    """
    try:
        from zerodaemon.agent import rag
        hits = rag.search(query, k=5)
        if not hits:
            return json.dumps({"query": query, "results": [], "message": "Knowledge base is empty or RAG not initialised"})
        return json.dumps({"query": query, "results": hits})
    except Exception as exc:
        return json.dumps({"query": query, "error": str(exc)})


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
        lc_tool(search_knowledge_base),
    ]
