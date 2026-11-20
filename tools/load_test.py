"""Hit the report server with many report runs at the same time and show how it copes.

    python tools/load_test.py --key <API key> --report SALES --formats csv,xlsx,pdf --hits 20 --concurrency 10 \
        --param Branch=KHI [--later] [--base http://127.0.0.1:8000]

* without --later every request waits for its file (POST /api/v1/reports/{id}/run); with many at once some get
  429/503 "busy" when every place for reports is taken — that is the server protecting its memory;
* with --later each request is answered at once (header X-Deliver: later), the server makes the files in the
  background and queues them when it is busy; the script then collects every file from /api/deliveries/{id}/file.

At the end it prints, per format: how many succeeded, failed or were turned away, the fastest/median/slowest time,
and the total bytes. While it runs, open the Designer or the Report Server: the pages should stay quick.
The API key needs the right to run the report (Admin → API keys); its requests-per-minute limit applies.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx


def one_run(client: httpx.Client, args: argparse.Namespace, fmt: str) -> dict:
    body = {"format": fmt, "parameters": dict(args.param)}
    url = f"/api/v1/reports/{args.report}/run"
    started = time.monotonic()
    try:
        if not args.later:
            response = client.post(url, json=body)
            return {"format": fmt, "status": response.status_code, "seconds": time.monotonic() - started,
                    "bytes": len(response.content) if response.status_code == 200 else 0,
                    "error": "" if response.status_code == 200 else response.text[:200]}
        response = client.post(url, json=body, headers={"X-Deliver": "later"})
        if response.status_code != 202:
            return {"format": fmt, "status": response.status_code, "seconds": time.monotonic() - started, "bytes": 0, "error": response.text[:200]}
        status_url = response.json()["delivery"]["statusUrl"]
        while True:
            delivery = client.get(status_url).json()["delivery"]
            if delivery["status"] in ("ready", "failed"):
                break
            time.sleep(0.5)
        if delivery["status"] == "failed":
            return {"format": fmt, "status": delivery["statusCode"] or 500, "seconds": time.monotonic() - started, "bytes": 0,
                    "error": delivery["error"][:200], "queued": delivery.get("waitedForPlace")}
        file = client.get(delivery["fileUrl"])
        return {"format": fmt, "status": file.status_code, "seconds": time.monotonic() - started, "bytes": len(file.content),
                "error": "", "queued": delivery.get("waitedForPlace")}
    except httpx.HTTPError as exc:
        return {"format": fmt, "status": 0, "seconds": time.monotonic() - started, "bytes": 0, "error": str(exc)[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--key", required=True, help="API key (Admin → API keys)")
    parser.add_argument("--report", required=True, help="report ID")
    parser.add_argument("--formats", default="csv,xlsx,pdf")
    parser.add_argument("--hits", type=int, default=10, help="runs per format")
    parser.add_argument("--concurrency", type=int, default=10, help="requests in flight at the same time")
    parser.add_argument("--param", action="append", default=[], type=lambda s: tuple(s.split("=", 1)), help="Name=value (repeatable)")
    parser.add_argument("--later", action="store_true", help="ask with X-Deliver: later and collect the files")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
    jobs = [fmt for fmt in formats for _ in range(args.hits)]
    print(f"{len(jobs)} runs of {args.report} ({', '.join(formats)}), {args.concurrency} at a time, "
          f"{'deliver later' if args.later else 'wait for the file'} → {args.base}")
    results: list[dict] = []
    started = time.monotonic()
    with httpx.Client(base_url=args.base, headers={"X-API-Key": args.key}, timeout=args.timeout) as client, \
            ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [pool.submit(one_run, client, args, fmt) for fmt in jobs]
        for done, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            mark = "ok " if result["status"] == 200 else f"{result['status']}"
            print(f"  [{done:>3}/{len(jobs)}] {result['format']:<4} {mark:<4} {result['seconds']:6.2f} s  {result['bytes']:>10,} B  {result['error']}")
    total = time.monotonic() - started
    print(f"\nAll done in {total:.1f} s")
    for fmt in formats:
        mine = [r for r in results if r["format"] == fmt]
        good = [r for r in mine if r["status"] == 200]
        busy = [r for r in mine if r["status"] in (429, 503)]
        times = sorted(r["seconds"] for r in good)
        line = f"  {fmt.upper():<5} ok {len(good)}/{len(mine)}  busy {len(busy)}  failed {len(mine) - len(good) - len(busy)}"
        if times:
            line += f"  fastest {times[0]:.2f} s  median {statistics.median(times):.2f} s  slowest {times[-1]:.2f} s"
            line += f"  {sum(r['bytes'] for r in good):,} B"
        if args.later:
            line += f"  waited in queue {sum(1 for r in mine if r.get('queued'))}"
        print(line)
    return 0 if all(r["status"] == 200 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
