"""Local antivirus discovery/scanning and VirusTotal hash-only reputation checks."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests

_VT_FILE_URL = "https://www.virustotal.com/api/v3/files/"
_VT_TIMEOUT = 15
_VT_MAX_BYTES = 1_000_000
_SCAN_TIMEOUT = 900
_POWERSHELL_QUERY = (
    "$ErrorActionPreference='Stop'; "
    "$items=Get-CimInstance -Namespace root/SecurityCenter2 "
    "-ClassName AntiVirusProduct | Select-Object -ExpandProperty displayName; "
    "ConvertTo-Json -InputObject @($items) -Compress"
)


def _defender_cli() -> Path | None:
    if sys.platform != "win32":
        return None
    candidates: list[Path] = []
    program_data = os.environ.get("ProgramData")
    if program_data:
        platform_dir = Path(program_data) / "Microsoft" / "Windows Defender" / "Platform"
        try:
            candidates.extend(platform_dir.glob("*/MpCmdRun.exe"))
        except (OSError, RuntimeError):
            pass
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Windows Defender" / "MpCmdRun.exe")
    existing = []
    for candidate in candidates:
        try:
            if candidate.is_file():
                existing.append(candidate)
        except OSError:
            continue
    if not existing:
        return None
    try:
        return max(existing, key=lambda candidate: candidate.stat().st_mtime)
    except OSError:
        return existing[-1]


def _windows_security_products() -> tuple[list[str] | None, str | None]:
    if sys.platform != "win32":
        return None, "Windows Security Center discovery is available only on Windows."
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_QUERY],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Windows Security Center query failed ({type(exc).__name__})."
    if result.returncode != 0:
        return None, "Windows Security Center query failed."
    try:
        values = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None, "Windows Security Center returned unreadable data."
    if values is None:
        return [], None
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return None, "Windows Security Center returned an unexpected response."
    products = [str(value).strip()[:200] for value in values if isinstance(value, str) and value.strip()]
    return products[:20], None


def _credential() -> str:
    from core.credentials import get_secret
    return get_secret("virustotal_api_key")


def antivirus_status() -> str:
    lines = ["Antivirus status (product registration is not a health or protection check):"]
    if sys.platform == "win32":
        products, error = _windows_security_products()
        if products:
            lines.append("Registered with Windows Security Center: " + "; ".join(products))
        elif error:
            lines.append(error)
        else:
            lines.append("Windows Security Center did not report a registered antivirus product.")
        lines.append(
            "Microsoft Defender scan command: "
            + ("available" if _defender_cli() else "not found")
        )
    else:
        lines.append("Windows Security Center product discovery is unavailable on this platform.")

    clamscan = shutil.which("clamscan")
    lines.append("ClamAV scan command: " + (clamscan or "not found"))
    try:
        has_vt_key = bool(_credential())
    except Exception as exc:
        lines.append(f"VirusTotal credential lookup failed ({type(exc).__name__}).")
        has_vt_key = False
    if has_vt_key:
        lines.append("VirusTotal hash lookup: API key configured; only SHA-256 hashes are sent.")
    elif not any("credential lookup failed" in line for line in lines):
        lines.append(
            "VirusTotal hash lookup: not configured. Set AETHER_VIRUSTOTAL_API_KEY "
            "or add virustotal_api_key through Aether's credential store/local config."
        )
    lines.append(
        "Aether can scan locally with Microsoft Defender or ClamAV when their command-line "
        "scanner is available. Other registered products are identified, but are not invoked "
        "unless a supported scanner adapter is available."
    )
    return "\n".join(lines)


def _target_file(value: str) -> tuple[Path | None, str | None]:
    if not isinstance(value, str) or not value.strip():
        return None, "Provide a file path."
    from core.protected_paths import DENIED, is_protected

    path = Path(value).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None, f"File not found or inaccessible: {path}"
    if is_protected(resolved):
        return None, DENIED
    if not resolved.is_file():
        return None, f"Path is not a file: {resolved}"
    return resolved, None


def _choose_scanner(provider: str) -> tuple[str | None, Path | str | None, str | None]:
    selected = (provider or "auto").strip().lower()
    if selected not in {"auto", "defender", "clamav"}:
        return None, None, "Unsupported provider. Choose auto, defender, or clamav."

    defender = _defender_cli() if sys.platform == "win32" else None
    clamscan = shutil.which("clamscan")
    if selected == "defender":
        if defender:
            return "defender", defender, None
        return None, None, "Microsoft Defender's MpCmdRun.exe was not found on this Windows system."
    if selected == "clamav":
        if clamscan:
            return "clamav", clamscan, None
        return None, None, "ClamAV's clamscan command was not found."

    if sys.platform == "win32":
        products, query_error = _windows_security_products()
        names = [name.lower() for name in products or []]
        if names:
            matches = []
            if defender and any("defender" in name or "microsoft" in name for name in names):
                matches.append(("defender", defender))
            if clamscan and any("clam" in name for name in names):
                matches.append(("clamav", clamscan))
            if len(products) == 1 and len(matches) == 1:
                return matches[0][0], matches[0][1], None
            if len(products) == 1 and not matches:
                return None, None, (
                    f"Windows Security Center lists {products[0]}, but Aether has no "
                    "compatible scan adapter for that product. No other scanner was started."
                )
            return None, None, (
                "Windows Security Center lists "
                + ", ".join(products or [])
                + ", but Aether cannot safely infer which scanner is active or primary. "
                  "Specify a supported provider explicitly; no scanner was started."
            )
        if query_error:
            return None, None, (
                f"{query_error} Specify provider=defender or provider=clamav explicitly; "
                "no scanner was started."
            )
    if clamscan:
        return "clamav", clamscan, None
    if defender:
        return "defender", defender, None
    return None, None, "No supported local scanner was found. Install/configure Defender or ClamAV."


def _scan(path: Path, provider: str) -> str:
    scanner, executable, error = _choose_scanner(provider)
    if error:
        return error
    if scanner == "clamav":
        command = [str(executable), "--no-summary", "--infected", "--", str(path)]
    else:
        command = [str(executable), "-Scan", "-ScanType", "3", "-File", str(path)]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_SCAN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"{scanner} scan timed out after {_SCAN_TIMEOUT} seconds."
    except OSError as exc:
        return f"Could not start {scanner} scanner ({type(exc).__name__})."

    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    output = output[-8000:] if output else "The scanner returned no details."
    if scanner == "clamav":
        if result.returncode == 0:
            return f"ClamAV: no detections reported for {path.name}.\n{output}"
        if result.returncode == 1:
            return f"ClamAV reported a detection in {path.name}. Review the scanner output and quarantine safely.\n{output}"
        return f"ClamAV scan failed (exit code {result.returncode}).\n{output}"
    return (
        f"Microsoft Defender scan finished with exit code {result.returncode} for {path.name}. "
        "Review Windows Security > Virus & threat protection > Protection history for findings "
        "or remediation; Defender may quarantine or remediate according to its policy.\n"
        f"{output}"
    )


def _hash_reputation(path: Path) -> str:
    try:
        api_key = _credential()
    except Exception as exc:
        return f"VirusTotal credential lookup failed ({type(exc).__name__}). No request was sent."
    if not api_key:
        return (
            "VirusTotal hash lookup is not configured. Set AETHER_VIRUSTOTAL_API_KEY or "
            "add virustotal_api_key through Aether's credential store/local config. "
            "No file was uploaded."
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        return f"Could not read file to calculate its SHA-256 hash ({type(exc).__name__})."

    sha256 = digest.hexdigest()
    try:
        response = requests.get(
            _VT_FILE_URL + sha256,
            headers={"x-apikey": api_key, "Accept": "application/json"},
            timeout=_VT_TIMEOUT,
            stream=True,
            allow_redirects=False,
        )
        try:
            if response.status_code == 404:
                return (
                    f"VirusTotal has no existing report for SHA-256 {sha256}. "
                    "Only the hash was sent; the file was not uploaded."
                )
            response.raise_for_status()
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _VT_MAX_BYTES:
                    return "VirusTotal response exceeded the size limit; no file was uploaded."
                chunks.append(chunk)
            data = json.loads(b"".join(chunks))
            if not isinstance(data, dict):
                raise ValueError("VirusTotal returned unexpected analysis data")
            record = data.get("data")
            if not isinstance(record, dict):
                raise ValueError("VirusTotal returned unexpected analysis data")
            attributes = record.get("attributes")
            if not isinstance(attributes, dict):
                raise ValueError("VirusTotal returned unexpected analysis data")
            stats = attributes.get("last_analysis_stats", {})
            if not isinstance(stats, dict):
                raise ValueError("VirusTotal returned unexpected analysis data")
        finally:
            response.close()
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (401, 403):
            return "VirusTotal rejected the API key. Check its validity and permissions."
        if status == 429:
            return "VirusTotal rate limit reached. Try again later."
        return f"VirusTotal lookup failed (HTTP {status or 'error'}). The file was not uploaded."
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        return f"VirusTotal lookup unavailable ({type(exc).__name__}). The file was not uploaded."

    labels = ("malicious", "suspicious", "harmless", "undetected", "timeout")
    try:
        counts = [f"{label}: {max(0, int(stats.get(label, 0) or 0))}" for label in labels]
    except (TypeError, ValueError):
        return "VirusTotal returned invalid analysis counts. The file was not uploaded."
    last_analysis = attributes.get("last_analysis_date")
    suffix = f"; report timestamp: {last_analysis}" if last_analysis else ""
    return (
        f"VirusTotal existing report for SHA-256 {sha256}: {', '.join(counts)}{suffix}. "
        "This is a third-party reputation signal, not a definitive verdict; only the hash "
        "was sent and the file was not uploaded."
    )


from core.risk import gated  # noqa: E402


@gated("antivirus")
def antivirus(parameters: dict, player=None, speak=None) -> str:
    """Report available protection, scan a file locally, or check its hash with VirusTotal."""
    action = str(parameters.get("action") or "status").strip().lower()
    if action == "status":
        return antivirus_status()
    path, error = _target_file(parameters.get("path", ""))
    if error:
        return error
    if action == "scan":
        return _scan(path, str(parameters.get("provider") or "auto"))
    if action == "hash_reputation":
        return _hash_reputation(path)
    return "Unknown antivirus action. Use status, scan, or hash_reputation."
