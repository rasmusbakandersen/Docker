#!/usr/bin/env python3
"""
Printarr — Self-hosted web interface for printing and scanning.
Wraps CUPS (printing) and SANE/scanimage (scanning) via FastAPI.

The Epson XP-2155 shares a single USB connection for printing and scanning.
To avoid USB contention, cupsd is fully stopped before each scan and
restarted afterward.
"""

import asyncio
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI(title="Printarr", version="3.0.0")

SCAN_DIR = Path(os.getenv("SCAN_DIR", "/app/scans"))
UPLOAD_DIR = Path("/tmp/printscan_uploads")
PAPERLESS_CONSUME = Path(os.getenv("PAPERLESS_CONSUME", "/paperless-consume"))
PRINTER_NAME = os.getenv("PRINTER_NAME", "Epson-XP2155")
SCAN_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Lock to prevent concurrent scan/print USB conflicts
_usb_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", "Command timed out"
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def stop_cups():
    """Fully stop cupsd to release the USB device for scanning."""
    # Kill all CUPS processes
    await run_cmd(["pkill", "-x", "cupsd"], timeout=5)
    await run_cmd(["pkill", "-f", "cups/backend/usb"], timeout=5)
    # Wait for USB device to be released
    await asyncio.sleep(2)


async def start_cups():
    """Restart cupsd after scanning."""
    await run_cmd(["/usr/sbin/cupsd"], timeout=5)
    # Wait for CUPS to be ready
    for _ in range(10):
        rc, out, _ = await run_cmd(["lpstat", "-r"], timeout=3)
        if rc == 0 and "running" in out.lower():
            return
        await asyncio.sleep(0.5)


def parse_scanner_list(output: str) -> list[dict]:
    scanners = []
    for line in output.strip().splitlines():
        m = re.match(r"device `(.+?)' is (.+)", line)
        if m:
            scanners.append({"device": m.group(1), "description": m.group(2).strip()})
    return scanners


def parse_lpstat_printers(output: str) -> list[dict]:
    printers = []
    for line in output.strip().splitlines():
        m = re.match(r"printer (\S+) (.+)", line)
        if m:
            name = m.group(1)
            status_text = m.group(2).strip()
            enabled = "idle" in status_text.lower() or "enabled" in status_text.lower()
            printers.append({"name": name, "status": status_text, "enabled": enabled})
    return printers


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(html_path.read_text())


# ---------------------------------------------------------------------------
# Printer endpoints
# ---------------------------------------------------------------------------

@app.get("/api/printers")
async def list_printers():
    rc, out, err = await run_cmd(["lpstat", "-p", "-d"])
    if rc != 0 and not out:
        return {"printers": [], "default": None, "error": err.strip()}
    printers = parse_lpstat_printers(out)
    default = None
    for line in out.splitlines():
        if line.startswith("system default destination:"):
            default = line.split(":")[-1].strip()
    return {"printers": printers, "default": default}


@app.post("/api/print")
async def print_file(
    file: UploadFile = File(...),
    printer: Optional[str] = Form(None),
    copies: int = Form(1),
    duplex: bool = Form(False),
    pages: Optional[str] = Form(None),
    color: bool = Form(True),
):
    suffix = Path(file.filename).suffix if file.filename else ".pdf"
    tmp_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    content = await file.read()
    tmp_path.write_bytes(content)

    cmd = ["lp"]
    if printer:
        cmd += ["-d", printer]
    cmd += ["-n", str(max(1, min(copies, 99)))]

    options = []
    if duplex:
        options.append("sides=two-sided-long-edge")
    if not color:
        options.append("ColorModel=Gray")
    if pages:
        cmd += ["-P", pages]
    for opt in options:
        cmd += ["-o", opt]

    cmd.append(str(tmp_path))

    async with _usb_lock:
        rc, out, err = await run_cmd(cmd)

    tmp_path.unlink(missing_ok=True)

    if rc != 0:
        raise HTTPException(status_code=500, detail=f"Print failed: {err.strip()}")

    job_id = None
    m = re.search(r"request id is (\S+)", out)
    if m:
        job_id = m.group(1)

    return {"success": True, "job_id": job_id, "message": out.strip()}


@app.get("/api/jobs")
async def list_jobs():
    rc, out, err = await run_cmd(["lpstat", "-o"])
    jobs = []
    for line in out.strip().splitlines():
        if line.strip():
            jobs.append(line.strip())
    return {"jobs": jobs}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    rc, out, err = await run_cmd(["cancel", job_id])
    if rc != 0:
        raise HTTPException(status_code=500, detail=err.strip())
    return {"success": True}


# ---------------------------------------------------------------------------
# Scanner endpoints
# ---------------------------------------------------------------------------

@app.get("/api/scanners")
async def list_scanners():
    async with _usb_lock:
        await stop_cups()
        try:
            rc, out, err = await run_cmd(["scanimage", "-L"], timeout=30)
        finally:
            await start_cups()
    if rc != 0:
        return {"scanners": [], "error": err.strip()}
    return {"scanners": parse_scanner_list(out)}


@app.post("/api/scan")
async def scan_document(
    device: Optional[str] = Form(None),
    resolution: int = Form(300),
    mode: str = Form("Color"),
    format: str = Form("png"),
    source: str = Form("Flatbed"),
):
    scan_id = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{uuid.uuid4().hex[:6]}"
    ext = "png" if format == "png" else "tiff" if format == "tiff" else "jpeg"
    filename = f"scan_{scan_id}.{ext}"
    output_path = SCAN_DIR / filename

    cmd = ["scanimage"]
    if device:
        cmd += ["-d", device]
    cmd += [
        f"--resolution={max(75, min(resolution, 1200))}",
        f"--mode={mode}",
        f"--format={ext if ext != 'jpeg' else 'jpeg'}",
        f"--source={source}",
        f"-o", str(output_path),
    ]

    async with _usb_lock:
        await stop_cups()
        try:
            rc, out, err = await run_cmd(cmd, timeout=180)
        finally:
            await start_cups()

    if rc != 0:
        raise HTTPException(status_code=500, detail=f"Scan failed: {err.strip()}")

    return {
        "success": True,
        "filename": filename,
        "path": f"/api/scans/{filename}",
        "size": output_path.stat().st_size,
    }


@app.get("/api/scans")
async def list_scans(limit: int = Query(50, ge=1, le=500)):
    files = sorted(SCAN_DIR.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
    scans = []
    for f in files[:limit]:
        if f.is_file() and f.suffix.lower() in (".png", ".jpeg", ".jpg", ".tiff", ".tif", ".pdf"):
            stat = f.stat()
            scans.append({
                "filename": f.name,
                "path": f"/api/scans/{f.name}",
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return {"scans": scans}


@app.get("/api/scans/{filename}")
async def get_scan(filename: str):
    safe_name = Path(filename).name
    file_path = SCAN_DIR / safe_name
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Scan not found")
    return FileResponse(file_path, filename=safe_name)


@app.delete("/api/scans/{filename}")
async def delete_scan(filename: str):
    safe_name = Path(filename).name
    file_path = SCAN_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Scan not found")
    file_path.unlink()
    return {"success": True}


# ---------------------------------------------------------------------------
# Paperless-NGX integration
# ---------------------------------------------------------------------------

@app.post("/api/scans/{filename}/paperless")
async def send_to_paperless(filename: str):
    safe_name = Path(filename).name
    src = SCAN_DIR / safe_name

    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="Scan not found")

    if not PAPERLESS_CONSUME.exists():
        raise HTTPException(
            status_code=500,
            detail="Paperless consume folder not mounted at " + str(PAPERLESS_CONSUME),
        )

    dst = PAPERLESS_CONSUME / safe_name
    if dst.exists():
        stem = dst.stem
        suffix = dst.suffix
        counter = 1
        while dst.exists():
            dst = PAPERLESS_CONSUME / f"{stem}_{counter}{suffix}"
            counter += 1

    try:
        shutil.copy2(str(src), str(dst))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to copy: {e}")

    return {
        "success": True,
        "message": f"Sent {safe_name} to Paperless-NGX",
        "destination": str(dst),
    }


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    cups_ok = (await run_cmd(["lpstat", "-r"]))[0] == 0
    sane_ok = (await run_cmd(["scanimage", "--version"]))[0] == 0
    paperless_ok = PAPERLESS_CONSUME.exists() and PAPERLESS_CONSUME.is_dir()
    return {
        "status": "ok" if cups_ok and sane_ok else "degraded",
        "cups": cups_ok,
        "sane": sane_ok,
        "paperless_consume": paperless_ok,
    }
