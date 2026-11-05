"""Sourced, read-only vulnerability briefs from OSV and CISA KEV."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests

_CVE_ID = re.compile(r"^CVE-\d{4}-\d{4,}$", re.I)
_OSV_URL = "https://api.osv.dev/v1/vulns/"
_CISA_KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)
_TIMEOUT = 12
_MAX_OSV_BYTES = 1_000_000
_MAX_KEV_BYTES = 20_000_000


def _fetch_json(url: str, max_bytes: int) -> dict:
    response = requests.get(
        url, timeout=_TIMEOUT, stream=True,
        headers={"Accept": "application/json", "User-Agent": "Aether-Threat-Intel/1.0"},
    )
    response.raise_for_status()
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("source response exceeded the size limit")
        chunks.append(chunk)
    data = json.loads(b"".join(chunks))
    if not isinstance(data, dict):
        raise ValueError("source returned an unexpected response")
    return data


def _references(vulnerability: dict) -> list[str]:
    references = []
    for item in vulnerability.get("references", []):
        url = item.get("url") if isinstance(item, dict) else None
        if isinstance(url, str) and url.startswith("https://") and url not in references:
            references.append(url)
        if len(references) == 5:
            break
    return references


def threat_intel_brief(cve_id: str) -> str:
    """Look up one CVE and cross-check whether CISA lists it as exploited."""
    identifier = str(cve_id or "").strip().upper()
    if not _CVE_ID.fullmatch(identifier):
        return "Invalid CVE ID. Provide an identifier such as CVE-2024-12345."

    retrieved = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"Vulnerability brief: {identifier}",
        f"Checked: {retrieved}",
        "Scope: public vulnerability records only; this does not scan your devices or determine whether you are affected.",
        "",
    ]

    osv = None
    try:
        osv = _fetch_json(_OSV_URL + quote(identifier, safe=""), _MAX_OSV_BYTES)
        if str(osv.get("id", "")).upper() != identifier:
            osv = None
            lines.append("OSV: response did not match the requested CVE; treated as unavailable.")
        else:
            summary = osv.get("summary") or osv.get("details") or "No summary provided."
            lines.append(f"OSV: {str(summary).strip()[:1200]}")
            if osv.get("modified"):
                lines.append(f"OSV modified: {osv['modified']}")
            affected = []
            for entry in osv.get("affected", [])[:8]:
                package = entry.get("package", {}) if isinstance(entry, dict) else {}
                name = package.get("name")
                ecosystem = package.get("ecosystem")
                if name:
                    affected.append(f"{ecosystem + '/' if ecosystem else ''}{name}"[:200])
            if affected:
                lines.append("Packages listed by OSV: " + ", ".join(affected))
            aliases = [
                alias for alias in osv.get("aliases", [])
                if isinstance(alias, str) and alias.upper() != identifier
            ][:10]
            if aliases:
                lines.append("Aliases: " + ", ".join(aliases))
            lines.append(f"OSV record: {_OSV_URL}{quote(identifier, safe='')}")
    except requests.HTTPError as exc:
        if getattr(getattr(exc, "response", None), "status_code", None) == 404:
            lines.append("OSV: no record found for this CVE ID.")
        else:
            lines.append(f"OSV lookup unavailable: {type(exc).__name__}.")
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        lines.append(f"OSV lookup unavailable: {type(exc).__name__}.")
    if osv is None and not any(line.startswith("OSV:") for line in lines):
        lines.append("OSV: no usable record was retrieved; absence is not evidence that the issue is harmless.")

    try:
        catalog = _fetch_json(_CISA_KEV_URL, _MAX_KEV_BYTES)
        entries = catalog.get("vulnerabilities", [])
        if not isinstance(entries, list):
            raise ValueError("CISA catalog has an unexpected format")
        if int(catalog.get("count", -1)) != len(entries):
            raise ValueError("CISA catalog count did not match its entries")
        match = next(
            (entry for entry in entries
             if isinstance(entry, dict) and entry.get("cveID", "").upper() == identifier),
            None,
        )
        if match:
            lines.extend([
                "",
                "CISA KEV status: LISTED as a known exploited vulnerability.",
                f"Date added: {match.get('dateAdded', 'not supplied')}",
                f"Vendor/product: {str(match.get('vendorProject', '?'))[:160]} / "
                f"{str(match.get('product', '?'))[:160]}",
                f"Required action: {str(match.get('requiredAction', 'See the CISA catalog entry.'))[:500]}",
                f"Due date: {match.get('dueDate', 'not supplied')}",
            ])
        else:
            lines.extend([
                "",
                "CISA KEV status: NOT LISTED in the retrieved catalog.",
                "This is not proof that the vulnerability has never been exploited.",
            ])
        lines.append(f"CISA KEV catalog: {_CISA_KEV_URL}")
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        lines.extend([
            "",
            f"CISA KEV status: unable to check ({type(exc).__name__}).",
            "Do not interpret a failed lookup as evidence that exploitation is absent.",
            f"CISA KEV catalog: {_CISA_KEV_URL}",
        ])

    references = _references(osv) if isinstance(osv, dict) else []
    if references:
        lines.extend(["", "OSV references:"])
        lines.extend(f"  {url}" for url in references)
    lines.extend([
        "",
        "Verify affected product/version and remediation with the vendor before acting. "
        "This brief is not a device scan or proof that a system is vulnerable.",
    ])
    return "\n".join(lines)
