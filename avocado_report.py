#!/usr/bin/env python3
"""
avocado_report_metrics_v12.py

Generate storage/memory test reports from Avocado job directories, including
key performance metrics by parsing per-test debug.log files.

This version improves:
- stdlog prefix stripping (handles continuation lines like "[stdlog]   ...")
- embedded JSON extraction for "Benchmark results:", "Filesystem test results:",
  "Application test results:"
- fio JSON parsing (collect full JSON object even if first line is just "{")
- kernel fio "✓ Pass N: ..." summary parsing (works with stdlog prefixes)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except Exception:
    canvas = None  # type: ignore


# ---------------------------- helpers ----------------------------

# Full stdlog prefix: [stdlog] 2026-01-14 21:12:20,986 avocado.test ... INFO | message
_STDLOG_FULL_RE = re.compile(r"^\[stdlog\]\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2},\d+\s+[^|]+\|\s+")
# Continuation lines used for pretty-printed JSON blocks: [stdlog]   "k": 1,
_STDLOG_CONT_RE = re.compile(r"^\[stdlog\]\s+")
# Some avocado process lines embed [stdout] or [stderr]
_BRACKET_STDOUT_RE = re.compile(r"\[stdout\]\s*(.*)$")
_BRACKET_STDERR_RE = re.compile(r"\[stderr\]\s*(.*)$")


def strip_stdlog(line: str) -> str:
    """Remove avocado stdlog prefix, handling both full and continuation lines."""
    line = line.rstrip("\n")
    if _STDLOG_FULL_RE.match(line):
        return _STDLOG_FULL_RE.sub("", line).strip()
    if _STDLOG_CONT_RE.match(line):
        # remove just "[stdlog]" and keep rest
        return _STDLOG_CONT_RE.sub("", line).strip()
    return line.strip()


def read_text_safe(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except Exception:
        return ""


def to_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def mib_per_s_from_kib_per_s(kib_s: Optional[float]) -> Optional[float]:
    if kib_s is None:
        return None
    return kib_s / 1024.0


# ---------------------------- parsing regex ----------------------------

# "✓ Pass 2: 2165.8 MB/s, 202s"
_RE_PASS_BW = re.compile(r"✓\s*Pass\s*\d+\s*:\s*([0-9]*\.?[0-9]+)\s*(MiB/s|MB/s)", re.IGNORECASE)

# OLTP: "✓ OLTP: 213841 read IOPS, 53454 write IOPS"
_RE_OLTP = re.compile(r"✓\s*OLTP:\s*([0-9]+)\s*read\s*IOPS,\s*([0-9]+)\s*write\s*IOPS", re.IGNORECASE)

# Mixed: "✓ Mixed 50/50: 244878 R + 244900 W = 489778 total IOPS"
_RE_MIXED = re.compile(
    r"✓\s*Mixed\s*50/50:\s*([0-9]+)\s*R\s*\+\s*([0-9]+)\s*W\s*=\s*([0-9]+)\s*total\s*IOPS",
    re.IGNORECASE,
)

# SQLite: "✓ SQLite insert rate: 357858.31 rows/s, select: 65.97 ops/s"
_RE_SQLITE = re.compile(
    r"✓\s*SQLite\s*insert\s*rate:\s*([0-9]*\.?[0-9]+)\s*rows/s,\s*select:\s*([0-9]*\.?[0-9]+)\s*ops/s",
    re.IGNORECASE,
)

# SPDK perf: "PCIE (...) :   20706.96    2588.37    6181.52 ..."
_RE_SPDK_ROW = re.compile(
    r"PCIE\s*\([0-9a-fA-F:.]+\)\s*NSID\s*\d+\s*from\s*core\s*\d+\s*:\s*([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)\s+([0-9]*\.?[0-9]+)",
    re.IGNORECASE,
)

# stress-ng metrc line (we target real-time bogo ops/s column)
# typical: "metrc: ... iomix ... <bogo ops> <real> <usr> <sys> <bogo ops/s real> <bogo ops/s usr+sys>"
_RE_STRESSNG_IOMIX = re.compile(r"metrc:.*\biomix\b.*\s([0-9]*\.?[0-9]+)\s*$", re.IGNORECASE)

# FIO JSON signature includes "fio version" and "jobs"
# We'll collect JSON blocks from stdout stream and then parse.

def _extract_stdout_stream(text: str) -> List[str]:
    """
    Extract raw stdout lines (without avocado prefixes) from debug.log.
    We accept:
      - process DEBUG lines: "... DEBUG| [stdout] ...."
      - plain printed stdout lines in debug.log (rare)
    """
    out: List[str] = []
    for raw in text.splitlines():
        s = raw
        # Keep original raw for bracket search
        m = _BRACKET_STDOUT_RE.search(raw)
        if m:
            out.append(m.group(1).rstrip())
            continue
        # Sometimes stdout lines appear without [stdout] but with stdlog prefix
        stripped = strip_stdlog(raw)
        # If it looks like JSON or known tool header, keep it
        if stripped.startswith("{") or stripped.startswith("fio") or stripped.startswith("stress-ng"):
            out.append(stripped)
    return out


def _collect_json_objects_from_stream(lines: List[str]) -> List[Dict[str, Any]]:
    """
    Collect JSON objects that may span multiple lines.
    We start collecting when we see a line beginning with '{' and stop when json.loads succeeds.
    """
    objs: List[Dict[str, Any]] = []
    buf: List[str] = []
    collecting = False

    def try_flush() -> None:
        nonlocal buf, collecting
        if not buf:
            return
        payload = "\n".join(buf).strip()
        try:
            o = json.loads(payload)
            if isinstance(o, dict):
                objs.append(o)
                buf = []
                collecting = False
        except Exception:
            # keep collecting
            pass

    for line in lines:
        s = line.strip()
        if not collecting:
            if s.startswith("{"):
                collecting = True
                buf = [s]
                try_flush()
        else:
            buf.append(s)
            try_flush()

    return objs


def _fio_metrics_from_json(obj: Dict[str, Any]) -> Dict[str, float]:
    """
    Pull aggregate fio metrics from fio JSON output.
    Returns keys:
      fio_rbw_mib_s, fio_wbw_mib_s, fio_riops, fio_wiops
    """
    res: Dict[str, float] = {}
    jobs = obj.get("jobs")
    if not isinstance(jobs, list):
        return res

    rbw_kib = 0.0
    wbw_kib = 0.0
    riops = 0.0
    wiops = 0.0

    for j in jobs:
        if not isinstance(j, dict):
            continue
        r = j.get("read", {})
        w = j.get("write", {})
        if isinstance(r, dict):
            rbw_kib += float(r.get("bw", 0.0) or 0.0)
            riops += float(r.get("iops", 0.0) or 0.0)
        if isinstance(w, dict):
            wbw_kib += float(w.get("bw", 0.0) or 0.0)
            wiops += float(w.get("iops", 0.0) or 0.0)

    # fio reports bw in KiB/s for JSON
    if rbw_kib > 0:
        res["fio_rbw_mib_s"] = rbw_kib / 1024.0
    if wbw_kib > 0:
        res["fio_wbw_mib_s"] = wbw_kib / 1024.0
    if riops > 0:
        res["fio_riops"] = riops
    if wiops > 0:
        res["fio_wiops"] = wiops
    return res


def _extract_embedded_json_block(text: str, marker: str) -> Optional[Dict[str, Any]]:
    """
    Extract a pretty-printed JSON object that follows a line containing `marker`,
    where subsequent lines are prefixed with "[stdlog]".
    """
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        if marker in raw:
            # start from this line; everything after the marker may include '{'
            # Build a JSON payload by stripping stdlog prefixes
            payload_lines: List[str] = []
            # get tail of the marker line after marker
            stripped = strip_stdlog(raw)
            pos = stripped.find(marker)
            tail = stripped[pos + len(marker):].strip()
            if tail:
                payload_lines.append(tail)
            # consume following lines until we reach a closing "}" that balances, or blank line after JSON
            for j in range(i + 1, min(i + 300, len(lines))):
                s = strip_stdlog(lines[j])
                if not s:
                    # allow blank lines after JSON
                    if payload_lines:
                        break
                    continue
                payload_lines.append(s)
                # quick stop if line is just "}" and we already started
                if s == "}" and payload_lines and "{" in "\n".join(payload_lines):
                    # attempt parse
                    payload = "\n".join(payload_lines).strip()
                    try:
                        return json.loads(payload)
                    except Exception:
                        continue
            # final attempt
            payload = "\n".join(payload_lines).strip()
            try:
                return json.loads(payload)
            except Exception:
                return None
    return None


# ---------------------------- data model ----------------------------

@dataclass
class TestRecord:
    job_id: str
    job_dir: str
    suite: str
    test_name: str
    status: str
    duration_s: float
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    # metrics
    spdk_iops: Optional[float] = None
    spdk_mib_s: Optional[float] = None
    spdk_lat_us: Optional[float] = None
    fio_rbw_mib_s: Optional[float] = None
    fio_wbw_mib_s: Optional[float] = None
    fio_riops: Optional[float] = None
    fio_wiops: Optional[float] = None
    tps: Optional[float] = None
    eps: Optional[float] = None
    p50_us: Optional[float] = None
    p99_us: Optional[float] = None


# ---------------------------- avocado results.json ----------------------------

def parse_results_json(job_dir: Path) -> Tuple[str, List[Dict[str, Any]]]:
    rj = job_dir / "results.json"
    obj = json.loads(read_text_safe(rj) or "{}")
    job_id = obj.get("job_id") or job_dir.name
    tests = obj.get("tests") or []
    if not isinstance(tests, list):
        tests = []
    return str(job_id), tests


def classify_suite(test_name: str) -> str:
    # simple heuristic
    low = test_name.lower()
    if "dimm" in low or "memory" in low:
        return "memory"
    if "storage" in low or "nvme" in low or "spdk" in low or "fio" in low:
        return "storage"
    return "other"


def find_debug_log(job_dir: Path, test_entry: Dict[str, Any]) -> Optional[Path]:
    # Prefer explicit logfile path from results.json
    lf = test_entry.get("logfile")
    if isinstance(lf, str) and lf:
        p = Path(lf)
        if p.exists():
            return p
    # Fallback: find under test-results by id
    tid = test_entry.get("id")
    if isinstance(tid, str) and tid:
        # id is like "01-storage_test_suite.py:Class.test"
        # directory is "01-storage_test_suite.py_Class.test"
        tr = job_dir / "test-results"
        if tr.exists():
            for d in tr.iterdir():
                if d.is_dir() and tid.split(":", 1)[0] in d.name and d.name.endswith(test_entry.get("name", "").replace(":", "_")):
                    cand = d / "debug.log"
                    if cand.exists():
                        return cand
            # more robust: look for matching name substring
            name = test_entry.get("name", "")
            for d in tr.iterdir():
                if d.is_dir() and name and name.replace(":", "_") in d.name:
                    cand = d / "debug.log"
                    if cand.exists():
                        return cand
    return None


# ---------------------------- per-test metric parsing ----------------------------

def parse_metrics_from_debuglog(test_name: str, debug_text: str) -> Dict[str, float]:
    m: Dict[str, float] = {}

    # 1) SPDK perf: find last Total line row with PCIE
    spdk_rows = []
    for raw in debug_text.splitlines():
        # parse from stdout lines too
        stripped = strip_stdlog(raw)
        mo = _RE_SPDK_ROW.search(stripped)
        if mo:
            spdk_rows.append((float(mo.group(1)), float(mo.group(2)), float(mo.group(3))))
    if spdk_rows:
        iops, mib_s, lat = spdk_rows[-1]
        m["spdk_iops"] = iops
        m["spdk_mib_s"] = mib_s
        m["spdk_lat_us"] = lat

    # 2) checkmark BW summaries (kernel full disk + some benchmarks)
    # Use average across passes if multiple
    bws: List[float] = []
    for raw in debug_text.splitlines():
        s = strip_stdlog(raw)
        mo = _RE_PASS_BW.search(s)
        if mo:
            bws.append(float(mo.group(1)))
    if bws:
        avg = sum(bws) / len(bws)
        # Decide whether read or write based on test name
        if "read" in test_name.lower():
            m["fio_rbw_mib_s"] = avg
        elif "write" in test_name.lower():
            m["fio_wbw_mib_s"] = avg

    # 3) OLTP / Mixed summary
    for raw in debug_text.splitlines():
        s = strip_stdlog(raw)
        mo = _RE_OLTP.search(s)
        if mo:
            m["fio_riops"] = float(mo.group(1))
            m["fio_wiops"] = float(mo.group(2))
        mo2 = _RE_MIXED.search(s)
        if mo2:
            m["fio_riops"] = float(mo2.group(1))
            m["fio_wiops"] = float(mo2.group(2))

    # 4) SQLite
    for raw in debug_text.splitlines():
        s = strip_stdlog(raw)
        mo = _RE_SQLITE.search(s)
        if mo:
            m["tps"] = float(mo.group(1))
            m["eps"] = float(mo.group(2))

    # 5) Embedded JSON blocks
    bench = _extract_embedded_json_block(debug_text, "Benchmark results:")
    if isinstance(bench, dict):
        lp = bench.get("latency_percentiles")
        if isinstance(lp, dict):
            # Prefer 50th_us / 99th_us
            p50 = lp.get("50th_us")
            p99 = lp.get("99th_us")
            if isinstance(p50, (int, float)):
                m["p50_us"] = float(p50)
            if isinstance(p99, (int, float)):
                m["p99_us"] = float(p99)

    fs = _extract_embedded_json_block(debug_text, "Filesystem test results:")
    if isinstance(fs, dict):
        fv = fs.get("fio_file_verify")
        if isinstance(fv, dict):
            jpath = fv.get("json")
            if isinstance(jpath, str) and jpath:
                jp = Path(jpath)
                if jp.exists():
                    try:
                        fio_obj = json.loads(read_text_safe(jp) or "{}")
                        m.update(_fio_metrics_from_json(fio_obj))
                    except Exception:
                        pass

    app = _extract_embedded_json_block(debug_text, "Application test results:")
    if isinstance(app, dict):
        # if suite ever emits app results json, optional
        pass

    # 6) stress-ng iomix bogo ops/s (real time) -> eps
    # We'll look for the metrc line that includes iomix and ends with a number
    for raw in debug_text.splitlines():
        s = strip_stdlog(raw)
        if "metrc:" in s and "iomix" in s:
            # last float on line is often bogo ops/s (usr+sys). Prefer real time column if present.
            # We'll capture the last number as EPS (good enough signal).
            nums = re.findall(r"([0-9]*\.?[0-9]+)", s)
            if nums:
                m["eps"] = float(nums[-2]) if len(nums) >= 2 else float(nums[-1])
                # In many builds: ... real time ... usr+sys ; choose real time (second last) when possible
                break

    # 7) Generic fio JSON parsing from stdout stream (covers sweeps, random tests)
    stdout_lines = _extract_stdout_stream(debug_text)
    fio_jsons = _collect_json_objects_from_stream(stdout_lines)
    # take the last fio JSON object that has "jobs"
    for obj in reversed(fio_jsons):
        if isinstance(obj, dict) and "jobs" in obj:
            m.update(_fio_metrics_from_json(obj))
            break

    return m


# ---------------------------- reporting ----------------------------

def write_csv(path: Path, rows: List[TestRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "job_id","job_dir","suite","test_name","status","duration_s",
            "spdk_iops","spdk_mib_s","spdk_lat_us",
            "fio_rbw_mib_s","fio_wbw_mib_s","fio_riops","fio_wiops",
            "tps","eps","p50_us","p99_us"
        ])
        for r in rows:
            w.writerow([
                r.job_id,r.job_dir,r.suite,r.test_name,r.status,f"{r.duration_s:.6f}",
                r.spdk_iops,r.spdk_mib_s,r.spdk_lat_us,
                r.fio_rbw_mib_s,r.fio_wbw_mib_s,r.fio_riops,r.fio_wiops,
                r.tps,r.eps,r.p50_us,r.p99_us
            ])


def fmt(x: Optional[float], nd: int = 2) -> str:
    if x is None:
        return "-"
    return f"{x:.{nd}f}"


def write_txt(path: Path, rows: List[TestRecord], title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    status_counts: Dict[str, int] = {}
    for r in rows:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    with path.open("w") as f:
        f.write(f"{title}\n")
        f.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"Total tests: {len(rows)}\n")
        if status_counts:
            f.write("Status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(status_counts.items())) + "\n")
        f.write("\n")
        header = (
            "STATUS  DUR(s)  SPDK_IOPS  SPDK_MiB/s  SPDK_lat(us)  "
            "FIO_RBW(MiB/s)  FIO_WBW(MiB/s)  FIO_RIOPS  FIO_WIOPS   "
            "TPS     EPS   P50(us)  P99(us)  TEST"
        )
        f.write(header + "\n")
        f.write("-" * len(header) + "\n")
        for r in rows:
            f.write(
                f"{r.status:<6} {r.duration_s:>7.3f} "
                f"{fmt(r.spdk_iops,2):>9} {fmt(r.spdk_mib_s,2):>11} {fmt(r.spdk_lat_us,2):>12} "
                f"{fmt(r.fio_rbw_mib_s,2):>13} {fmt(r.fio_wbw_mib_s,2):>13} "
                f"{fmt(r.fio_riops,0):>9} {fmt(r.fio_wiops,0):>9} "
                f"{fmt(r.tps,2):>7} {fmt(r.eps,2):>7} "
                f"{fmt(r.p50_us,3):>7} {fmt(r.p99_us,3):>7}  "
                f"{r.test_name}\n"
            )


def write_pdf(path: Path, rows: List[TestRecord], title: str) -> None:
    if canvas is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, y, title)
    y -= 18
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    y -= 22

    cols = ["STATUS","DUR(s)","SPDK_IOPS","SPDK_MiB/s","FIO_RBW","FIO_WBW","RIOPS","WIOPS","TPS","EPS","P50us","P99us","TEST"]
    colw = [50,55,70,70,55,55,45,45,45,45,45,45, 250]
    c.setFont("Helvetica-Bold", 8)
    x = 50
    for w, name in zip(colw, cols):
        c.drawString(x, y, name)
        x += w
    y -= 12
    c.setFont("Helvetica", 7)
    for r in rows:
        if y < 60:
            c.showPage()
            y = height - 50
            c.setFont("Helvetica-Bold", 8)
            x = 50
            for w, name in zip(colw, cols):
                c.drawString(x, y, name)
                x += w
            y -= 12
            c.setFont("Helvetica", 7)
        vals = [
            r.status,
            f"{r.duration_s:.1f}",
            fmt(r.spdk_iops,0),
            fmt(r.spdk_mib_s,1),
            fmt(r.fio_rbw_mib_s,1),
            fmt(r.fio_wbw_mib_s,1),
            fmt(r.fio_riops,0),
            fmt(r.fio_wiops,0),
            fmt(r.tps,1),
            fmt(r.eps,1),
            fmt(r.p50_us,1),
            fmt(r.p99_us,1),
            r.test_name,
        ]
        x = 50
        for w, v in zip(colw, vals):
            c.drawString(x, y, str(v)[:40])
            x += w
        y -= 10
    c.save()


# ---------------------------- main ----------------------------

def list_recent_jobs(job_root: Path, n: int) -> List[Path]:
    jobs = [p for p in job_root.glob("job-*") if p.is_dir()]
    jobs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return jobs[:n]


def build_records_for_job(job_dir: Path, debug: bool=False) -> List[TestRecord]:
    job_id, tests = parse_results_json(job_dir)
    rows: List[TestRecord] = []
    for t in tests:
        name = t.get("name") or ""
        status = t.get("status") or "UNKNOWN"
        dur = float(t.get("time_elapsed") or 0.0)
        suite = classify_suite(name)
        rec = TestRecord(
            job_id=str(job_id),
            job_dir=str(job_dir),
            suite=suite,
            test_name=str(name),
            status=str(status),
            duration_s=dur,
            start_time=to_float(t.get("actual_time_start")),
            end_time=to_float(t.get("actual_time_end")),
        )
        dbg = find_debug_log(job_dir, t)
        if dbg and dbg.exists():
            text = read_text_safe(dbg)
            metrics = parse_metrics_from_debuglog(rec.test_name, text)
            for k, v in metrics.items():
                setattr(rec, k, v)
            if debug and not metrics:
                print(f"[debug] no metrics parsed for: {rec.test_name}")
        else:
            if debug:
                print(f"[debug] missing debug.log for: {rec.test_name}")
        rows.append(rec)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", type=str, default="", help="Specific Avocado job directory to parse")
    ap.add_argument("--job-root", type=str, default="/home/ubuntu/avocado/job-results", help="Root directory containing job-* dirs")
    ap.add_argument("--jobs", type=int, default=1, help="Number of most recent jobs to scan (ignored if --job-dir set)")
    ap.add_argument("--min-tests", type=int, default=2, help="Ignore jobs with fewer than this many tests (only in --jobs mode)")
    ap.add_argument("--out-dir", type=str, default="./reports", help="Output directory")
    ap.add_argument("--debug", action="store_true", help="Debug logging")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    job_dirs: List[Path] = []
    if args.job_dir:
        job_dirs = [Path(args.job_dir)]
    else:
        job_dirs = list_recent_jobs(Path(args.job_root), args.jobs)

    all_rows: List[TestRecord] = []
    for jd in job_dirs:
        try:
            job_id, tests = parse_results_json(jd)
            if not args.job_dir and len(tests) < args.min_tests:
                if args.debug:
                    print(f"[debug] {jd.name}: ignored (tests={len(tests)} < min_tests={args.min_tests})")
                continue
            rows = build_records_for_job(jd, debug=args.debug)
            all_rows.extend(rows)
            if args.debug:
                print(f"[debug] {jd.name}: parsed {len(rows)} tests from results.json (raw tests={len(tests)})")
        except Exception as e:
            if args.debug:
                print(f"[debug] failed job {jd}: {e}")

    # split by suite
    storage = [r for r in all_rows if r.suite == "storage"]
    memory = [r for r in all_rows if r.suite == "memory"]

    write_csv(out_dir / "storage_report.csv", storage)
    write_txt(out_dir / "storage_report.txt", storage, "Storage Test Report")
    write_pdf(out_dir / "storage_report.pdf", storage, "Storage Test Report")

    write_csv(out_dir / "memory_report.csv", memory)
    write_txt(out_dir / "memory_report.txt", memory, "Memory (DIMM) Test Report")
    write_pdf(out_dir / "memory_report.pdf", memory, "Memory (DIMM) Test Report")

    print(f"Wrote reports to: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
