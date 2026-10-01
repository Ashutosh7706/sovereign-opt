"""JUnit XML -> self-contained HTML test report for a data room (audit #93/#113). No extra deps.

    cd backend && python -m pytest tests --junitxml=junit.xml --cov=sovereign --cov-report=xml
    python scripts/test_report.py junit.xml ../docs/TEST_REPORT.html [coverage.xml]
"""
import datetime
import html
import platform
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def main(junit: str, out: str, coverage: str | None = None):
    root = ET.parse(junit).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    by_file = defaultdict(lambda: {"pass": 0, "fail": 0, "skip": 0, "time": 0.0, "failures": []})
    for s in suites:
        for tc in s.findall("testcase"):
            f = tc.get("classname", "?").split(".")[-1]
            d = by_file[f]
            d["time"] += float(tc.get("time", 0))
            if tc.find("failure") is not None or tc.find("error") is not None:
                d["fail"] += 1
                d["failures"].append(tc.get("name"))
            elif tc.find("skipped") is not None:
                d["skip"] += 1
            else:
                d["pass"] += 1
    tot = {k: sum(v[k] for v in by_file.values()) for k in ("pass", "fail", "skip")}
    cov_rows = ""
    if coverage and Path(coverage).exists():
        c = ET.parse(coverage).getroot()
        cov_rows = f"<p>Line coverage (solver package): <b>{float(c.get('line-rate', 0)) * 100:.1f}%</b></p><table>" \
                   "<tr><th>module</th><th>lines</th></tr>"
        for cls in sorted(c.iter("class"), key=lambda e: e.get("filename")):
            cov_rows += f"<tr><td>{html.escape(cls.get('filename'))}</td><td>{float(cls.get('line-rate')) * 100:.0f}%</td></tr>"
        cov_rows += "</table>"
    rows = "".join(
        f"<tr class={'bad' if v['fail'] else 'ok'}><td>{html.escape(k)}</td><td>{v['pass']}</td><td>{v['fail']}</td>"
        f"<td>{v['skip']}</td><td>{v['time']:.1f}s</td><td>{html.escape(', '.join(v['failures'][:5]))}</td></tr>"
        for k, v in sorted(by_file.items()))
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Test report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#111;background:#fff}}table{{border-collapse:collapse;margin:12px 0}}
td,th{{border:1px solid #ccc;padding:4px 10px;text-align:left}}tr.bad td{{background:#fde8e8}}.big{{font-size:22px}}</style></head><body>
<h1>Sovereign Optimizer - automated test report</h1>
<p>{datetime.datetime.now().isoformat(timespec='seconds')} · {html.escape(platform.platform())} · Python {platform.python_version()}</p>
<p class="big">{tot['pass']} passed · {tot['fail']} failed · {tot['skip']} skipped</p>
<p>Correctness oracle: HiGHS (independent open-source solver) on the same models; see backend/tests/README.md for
the mapping of tests to roadmap items. Skipped tests need hardware not present on this machine (GPU).</p>
<table><tr><th>test file</th><th>passed</th><th>failed</th><th>skipped</th><th>time</th><th>failures</th></tr>{rows}</table>
{cov_rows}</body></html>"""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(doc, encoding="utf-8")
    print(f"wrote {out}: {tot}")
    return 1 if tot["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
