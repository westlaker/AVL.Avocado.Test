#!/usr/bin/env python3
import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Optional PDF support
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    PDF_AVAILABLE = True
except Exception:
    PDF_AVAILABLE = False


@dataclass
class TestRecord:
    job_id: str
    job_dir: str
    suite: str            # "storage" or "memory" (heuristic)
    test_name: str
    status: str
    duration_s: Optional[float]
    start_time: Optional[str]
    end_time: Optional[str]
    fio_bw_kib: Optional[float] = None
    fio_iops: Optional[float] = None
    spdk_iops: Optional[float] = None
    spdk_mb_s: Optional[float] = None
    error_tail: str = ""


STATUS_RE = re.compile(r"^\s*\(\d+/\d+\)\s+(?P<test>.+?):\s+(?P<status>PASS|FAIL|ERROR|CANCEL|SKIP|WARN|INTERRUPT)\s*(?:\((?P<dur>[\d.]+)\s*s\))?\s*$")
START_RE = re.compile(r"^\s*\(\d+/\d+\)\s+(?P<test>.+?):\s+STARTED\s*$")


def guess_suite(test_name: str) -> str:
    t = test_name.lower()
    if "dimm" in t or "memory" in t:
        return "memory"
    if "storage" in t or "nvme" in t or "spdk" in t or "fio" in t:
        return "storage"
    # fallback: class names often include StorageKernelTests / DIMMTests etc.
    if "storage" in test_name:
        return "storage"
    return "memory"  # conservative default


def find_jobs(job_root: Path, limit: int = 20) -> List[Path]:
    if not job_root.exists():
        return []
    jobs = sorted([p for p in job_root.glob("job-*") if p.is_dir()],
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return jobs[:limit]


def read_text(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        data = path.read_bytes()
        if len(data) > max_bytes:
            data = data[-max_bytes:]
        return data.decode(errors="replace")
    except Exception:
        return ""


def parse_job_log(job_dir: Path) -> List[TestRecord]:
    """Parse job.log lines like '(08/16) ...: PASS (12.34 s)'."""
    job_log = job_dir / "job.log"
    txt = read_text(job_log)
    if not txt:
        return []

    job_id = job_dir.name
    records: Dict[str, TestRecord] = {}
    start_seen: Dict[str, str] = {}

    # Try to extract a coarse job start/end time from log headers (best-effort)
    # Avocado job.log typically includes timestamps in lines; we keep per-test times None for now.
    for line in txt.splitlines():
        mstart = START_RE.match(line)
        if mstart:
            tname = mstart.group("test").strip()
            start_seen[tname] = start_seen.get(tname, "")
            # We can’t reliably parse exact timestamps from this short line alone.

        m = STATUS_RE.match(line)
        if not m:
            continue
        tname = m.group("test").strip()
        status = m.group("status").strip()
        dur = m.group("dur")
        dur_s = float(dur) if dur else None
        suite = guess_suite(tname)

        records[tname] = TestRecord(
            job_id=job_id,
            job_dir=str(job_dir),
            suite=suite,
            test_name=tname,
            status=status,
            duration_s=dur_s,
            start_time=None,
            end_time=None,
        )

    # Attach error tails from per-test debug.log when not PASS
    for tname, rec in records.items():
        if rec.status == "PASS":
            continue
        debug = find_debug_log(job_dir, tname)
        if debug:
            tail = tail_lines(read_text(debug), 60)
            rec.error_tail = tail

        # parse metrics for both PASS and non-PASS if logs exist
        debug2 = debug or find_debug_log(job_dir, tname)
        if debug2:
            apply_perf_parsing(rec, read_text(debug2))

    return list(records.values())


def find_debug_log(job_dir: Path, test_name: str) -> Optional[Path]:
    """Find matching test-results/*/debug.log by substring heuristics."""
    tr = job_dir / "test-results"
    if not tr.exists():
        return None
    # Prefer exact-ish match
    candidates = []
    for d in tr.iterdir():
        if not d.is_dir():
            continue
        dbg = d / "debug.log"
        if not dbg.exists():
            continue
        # directory names often include the test name sanitized; use substring matching
        if sanitize(test_name) in d.name or test_name.split(":")[-1] in d.name:
            candidates.append(dbg)
    if candidates:
        # pick newest
        return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    # fallback: just return any debug.log (not great)
    all_dbg = list(tr.glob("*/debug.log"))
    if not all_dbg:
        return None
    return sorted(all_dbg, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s)


def tail_lines(text: str, n: int) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n:])


def apply_perf_parsing(rec: TestRecord, debug_text: str) -> None:
    """
    Best-effort parsing:
    - fio: looks for 'READ:' or 'WRITE:' lines containing IOPS= and BW=
    - spdk_nvme_perf: looks for 'IOPS' and 'MB/s' patterns
    """
    # fio sample: READ: bw=123MiB/s (129MB/s), 31.5k IOPS
    fio_bw = None
    fio_iops = None

    # more general fio regexes
    fio_line_re = re.compile(r"\b(READ|WRITE):.*?\bIOPS=([0-9.]+)([kKmM]?)\b.*?\bbw=([0-9.]+)\s*([KMG]iB/s|[KMG]B/s)\b")
    for line in debug_text.splitlines():
        m = fio_line_re.search(line)
        if m:
            iops_val = float(m.group(2))
            iops_suffix = m.group(3).lower()
            if iops_suffix == "k":
                iops_val *= 1_000
            elif iops_suffix == "m":
                iops_val *= 1_000_000
            fio_iops = iops_val

            bw_val = float(m.group(4))
            bw_unit = m.group(5)
            # Convert to KiB/s
            mult = 1.0
            if "KiB/s" in bw_unit:
                mult = 1.0
            elif "MiB/s" in bw_unit:
                mult = 1024.0
            elif "GiB/s" in bw_unit:
                mult = 1024.0 * 1024.0
            elif "KB/s" in bw_unit:
                mult = 1000.0 / 1024.0
            elif "MB/s" in bw_unit:
                mult = 1000.0 * 1000.0 / 1024.0
            elif "GB/s" in bw_unit:
                mult = 1000.0 * 1000.0 * 1000.0 / 1024.0
            fio_bw = bw_val * mult

    # SPDK perf patterns (varies by version)
    spdk_iops = None
    spdk_mb_s = None
    spdk_iops_re = re.compile(r"\bIOPS[:=]?\s*([0-9.]+)\s*([kKmM]?)\b")
    spdk_mb_re = re.compile(r"\bMB/s[:=]?\s*([0-9.]+)\b")
    for line in debug_text.splitlines():
        mi = spdk_iops_re.search(line)
        if mi:
            val = float(mi.group(1))
            suf = mi.group(2).lower()
            if suf == "k":
                val *= 1_000
            elif suf == "m":
                val *= 1_000_000
            spdk_iops = val
        mm = spdk_mb_re.search(line)
        if mm:
            spdk_mb_s = float(mm.group(1))

    rec.fio_bw_kib = fio_bw
    rec.fio_iops = fio_iops
    rec.spdk_iops = spdk_iops
    rec.spdk_mb_s = spdk_mb_s


def write_csv(path: Path, rows: List[TestRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "job_id", "job_dir", "suite", "test_name", "status", "duration_s",
            "start_time", "end_time",
            "fio_bw_kib_s", "fio_iops", "spdk_iops", "spdk_mb_s",
            "error_tail"
        ])
        for r in rows:
            w.writerow([
                r.job_id, r.job_dir, r.suite, r.test_name, r.status, r.duration_s,
                r.start_time, r.end_time,
                r.fio_bw_kib, r.fio_iops, r.spdk_iops, r.spdk_mb_s,
                r.error_tail
            ])


def write_text_report(path: Path, rows: List[TestRecord], title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = len(rows)
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    lines = []
    lines.append(f"{title}")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"Total tests: {total}")
    lines.append("Status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    lines.append("")

    # Fail/CANCEL detail first
    bad = [r for r in rows if r.status != "PASS"]
    if bad:
        lines.append("Non-PASS tests:")
        for r in bad:
            lines.append(f"- {r.status:7s}  {r.test_name}  (job={r.job_id})  dur={r.duration_s}")
        lines.append("")

        for r in bad:
            lines.append("=" * 80)
            lines.append(f"{r.status} :: {r.test_name}")
            lines.append(f"Job: {r.job_id}")
            if r.fio_iops or r.fio_bw_kib or r.spdk_iops or r.spdk_mb_s:
                lines.append("Metrics:")
                if r.fio_bw_kib:
                    lines.append(f"  fio_bw_kib_s: {r.fio_bw_kib:.1f}")
                if r.fio_iops:
                    lines.append(f"  fio_iops: {r.fio_iops:.1f}")
                if r.spdk_iops:
                    lines.append(f"  spdk_iops: {r.spdk_iops:.1f}")
                if r.spdk_mb_s:
                    lines.append(f"  spdk_mb_s: {r.spdk_mb_s:.1f}")
            if r.error_tail:
                lines.append("")
                lines.append("debug.log tail:")
                lines.append(r.error_tail)
            lines.append("")

    # PASS summary with top perf if available
    perf = [r for r in rows if r.status == "PASS" and (r.fio_iops or r.spdk_iops)]
    if perf:
        lines.append("")
        lines.append("Top performance (best-effort parsed):")
        # sort by whichever is present
        perf_sorted = sorted(perf, key=lambda r: (r.spdk_iops or 0, r.fio_iops or 0), reverse=True)[:10]
        for r in perf_sorted:
            lines.append(
                f"- {r.test_name}  fio_iops={r.fio_iops} fio_bw_kib_s={r.fio_bw_kib}  "
                f"spdk_iops={r.spdk_iops} spdk_mb_s={r.spdk_mb_s}"
            )

    path.write_text("\n".join(lines), encoding="utf-8")


def write_pdf(path: Path, rows: List[TestRecord], title: str) -> None:
    if not PDF_AVAILABLE:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter

    def draw_line(y: float, text: str, size: int = 10) -> float:
        c.setFont("Helvetica", size)
        c.drawString(40, y, text[:120])
        return y - (size + 2)

    y = height - 40
    y = draw_line(y, title, 14)
    y = draw_line(y, f"Generated: {datetime.now().isoformat(timespec='seconds')}", 10)
    y -= 10

    total = len(rows)
    by_status: Dict[str, int] = {}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    y = draw_line(y, f"Total tests: {total}", 10)
    y = draw_line(y, "Status: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())), 10)
    y -= 10

    bad = [r for r in rows if r.status != "PASS"]
    y = draw_line(y, f"Non-PASS tests: {len(bad)}", 11)
    for r in bad[:25]:
        if y < 80:
            c.showPage()
            y = height - 40
        y = draw_line(y, f"{r.status:7s} {r.test_name} (job={r.job_id})", 9)

    c.showPage()
    y = height - 40
    y = draw_line(y, "Failure details (debug.log tail excerpts)", 12)
    for r in bad[:10]:
        if y < 120:
            c.showPage()
            y = height - 40
        y = draw_line(y, f"{r.status} :: {r.test_name}", 10)
        y = draw_line(y, f"Job: {r.job_id}", 9)
        if r.error_tail:
            for line in r.error_tail.splitlines()[-25:]:
                if y < 80:
                    c.showPage()
                    y = height - 40
                y = draw_line(y, line, 7)
        y -= 8

    c.save()


def main():
    ap = argparse.ArgumentParser(description="Collect Avocado job results into storage/memory reports (CSV/TXT/PDF).")
    ap.add_argument("--job-root", default=str(Path.home() / "avocado" / "job-results"),
                    help="Path to avocado job-results directory (default: ~/avocado/job-results)")
    ap.add_argument("--out-dir", default="reports", help="Output directory")
    ap.add_argument("--jobs", type=int, default=10, help="How many newest jobs to scan")
    ap.add_argument("--no-pdf", action="store_true", help="Disable PDF output")
    args = ap.parse_args()

    job_root = Path(args.job_root).expanduser()
    out_dir = Path(args.out_dir)

    jobs = find_jobs(job_root, limit=args.jobs)
    if not jobs:
        print(f"No Avocado jobs found under: {job_root}", file=sys.stderr)
        sys.exit(2)

    all_rows: List[TestRecord] = []
    for job in jobs:
        rows = parse_job_log(job)
        all_rows.extend(rows)

    storage = [r for r in all_rows if r.suite == "storage"]
    memory = [r for r in all_rows if r.suite == "memory"]

    # Write reports
    write_csv(out_dir / "storage_report.csv", storage)
    write_text_report(out_dir / "storage_report.txt", storage, "Storage Test Report")
    write_csv(out_dir / "memory_report.csv", memory)
    write_text_report(out_dir / "memory_report.txt", memory, "Memory (DIMM) Test Report")

    if not args.no_pdf and PDF_AVAILABLE:
        write_pdf(out_dir / "storage_report.pdf", storage, "Storage Test Report")
        write_pdf(out_dir / "memory_report.pdf", memory, "Memory (DIMM) Test Report")

    print(f"Wrote reports to: {out_dir.resolve()}")
    if not PDF_AVAILABLE and not args.no_pdf:
        print("Note: PDF not generated (reportlab not installed in this environment). Use --no-pdf to silence.")


if __name__ == "__main__":
    main()

