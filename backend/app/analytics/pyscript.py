"""Python scripts in Analytics: clean, reshape or model datasets with pandas, scikit-learn and scipy.

A script reads one to five datasets (`df`, `dfs["name"]`) and leaves a DataFrame in `result`. It can be tried
(run → the first rows, the printed output) or saved as a dataset of its own (sourceKind "python"): refreshing that
dataset runs the script again on the current data of its inputs, so it can be scheduled like any other dataset.

Each run is a separate Python process (pysandbox.py) with an empty environment, its own temporary folder, a time
limit and a memory limit; it never sees the service's settings or files. Only accounts with the Analytics and the
Python right may run scripts (routers/analytics.py), and every run is in the audit trail.

    ANALYTICS_PYTHON_TIMEOUT_SECONDS   a script is stopped after this (default 120)
    ANALYTICS_PYTHON_MEMORY_MB         memory one script may use (default 2048; 0 = no limit)
    ANALYTICS_PYTHON_MAX_ROWS          most rows a script reads from one dataset and returns (default 1,000,000)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from backend.app.analytics import settings
from backend.app.logs import LOG

SANDBOX = Path(__file__).with_name("pysandbox.py")
CONNECTION, LANGUAGE = "python", "pandas"        # what a Python dataset has as its data adapter and query language
MAX_SCRIPT = 100_000
MAX_INPUTS = 5
PRELOAD = ("sklearn", "sklearn.preprocessing", "sklearn.impute", "sklearn.cluster", "sklearn.linear_model", "sklearn.ensemble",
           "sklearn.model_selection", "sklearn.metrics", "sklearn.decomposition", "scipy", "scipy.stats", "scipy.signal",
           "scipy.interpolate", "statistics", "re", "datetime", "math", "json", "collections", "itertools")
EXAMPLE = '''# df = the first dataset, dfs["Name"] = any of them. pd = pandas, np = numpy.
# Leave the cleaned data in `result`; it becomes a new dataset when you save.
result = (
    df.drop_duplicates()
      .dropna(how="all")
)
print(result.shape, "rows x columns")
'''


def timeout_seconds() -> int:
    return settings._int("ANALYTICS_PYTHON_TIMEOUT_SECONDS", 120, 5, 3600)


def memory_mb() -> int:
    return settings._int("ANALYTICS_PYTHON_MEMORY_MB", 2048, 0, 262_144)


def max_rows() -> int:
    return settings._int("ANALYTICS_PYTHON_MAX_ROWS", 1_000_000, 10, 50_000_000)


def available() -> str:
    """"" when scripts can run here, else why not (a package is missing)."""
    try:
        import pandas  # noqa: F401, PLC0415
    except ImportError:
        return "pandas is not installed on this server (pip install -r requirements.txt)"
    return ""


def clean_source(payload: dict[str, Any], find: Callable[[str], dict[str, Any] | None], own_id: str = "") -> dict[str, Any]:
    """The source of a Python dataset: the script and its input datasets (IDs, 1–5, not itself)."""
    script = payload.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ValueError("Write the Python script")
    if len(script) > MAX_SCRIPT:
        raise ValueError(f"The script is longer than {MAX_SCRIPT:,} characters")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError("Choose the dataset(s) the script reads")
    if len(inputs) > MAX_INPUTS:
        raise ValueError(f"A script reads at most {MAX_INPUTS} datasets")
    clean: list[str] = []
    for value in inputs:
        dataset_id = str(value or "")
        item = find(dataset_id)
        if item is None:
            raise ValueError(f"Dataset {dataset_id or '(empty)'} not found")
        if own_id and (dataset_id == own_id or own_id in depends_on(item, find)):
            raise ValueError(f"“{item.get('name')}” is made from this dataset; a script cannot read its own result")
        if dataset_id not in clean:
            clean.append(dataset_id)
    return {"sourceKind": "python", "script": script.replace("\r\n", "\n"), "inputs": clean}


def depends_on(item: dict[str, Any], find: Callable[[str], dict[str, Any] | None], depth: int = 0) -> set[str]:
    """Every dataset a Python dataset is made from (its inputs, theirs, …)."""
    if item.get("sourceKind") != "python" or depth > 10:
        return set()
    found: set[str] = set()
    for dataset_id in item.get("inputs") or []:
        found.add(dataset_id)
        parent = find(dataset_id)
        if parent is not None:
            found |= depends_on(parent, find, depth + 1)
    return found


def _environment() -> dict[str, str]:
    """An almost empty environment: nothing of the service's (passwords, keys, connection strings) is passed on."""
    keep = {name: os.environ[name] for name in ("SYSTEMROOT", "SystemRoot", "WINDIR", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
            if name in os.environ}
    threads = str(settings.threads())
    return {**keep, "PATH": str(Path(sys.executable).parent), "PYTHONIOENCODING": "utf-8", "OMP_NUM_THREADS": threads,
            "OPENBLAS_NUM_THREADS": threads, "MKL_NUM_THREADS": threads, "LOKY_MAX_CPU_COUNT": threads, "PYTHONHASHSEED": "0"}


def _write_input(dataset: dict[str, Any], target: Path, limit: int) -> None:
    from backend.app.analytics import importer  # noqa: PLC0415
    path = importer.file_path(dataset.get("file") or "")
    if path is None or not path.exists():
        raise ValueError(f"The dataset “{dataset.get('name')}” has no data yet: refresh it first")
    con = importer.connect()
    try:
        frame = con.execute(f"SELECT * FROM read_parquet({importer.quote_path(path)}) LIMIT {limit + 1}").df()
    finally:
        con.close()
    if len(frame) > limit:
        raise ValueError(f"“{dataset.get('name')}” has more than {limit:,} rows (ANALYTICS_PYTHON_MAX_ROWS); "
                         "make a smaller dataset for the script")
    frame.to_pickle(target)


def run(script: str, inputs: list[dict[str, Any]], *, output: Path | None = None, preview_rows: int = 50) -> dict[str, Any]:
    """Run a script on the input datasets (records with a Parquet file).

    Returns {"ok", "error", "output", "notes", "columns", "rowCount", "rows" (the first preview_rows), "durationMs"}.
    With `output`, a successful result is also written there as a Parquet file. Raises ValueError when it cannot start
    (no data, too many rows) or ran out of time."""
    missing = available()
    if missing:
        raise ValueError(missing)
    started = time.monotonic()
    limit = max_rows()
    work = Path(tempfile.mkdtemp(prefix="rs-py-"))
    try:
        files = []
        for index, dataset in enumerate(inputs):
            name = f"in{index}.pkl"
            _write_input(dataset, work / name, limit)
            files.append({"file": name, "name": str(dataset.get("name") or dataset["id"]), "id": dataset["id"]})
        (work / "script.py").write_text(script, encoding="utf-8")
        (work / "job.json").write_text(json.dumps({"inputs": files, "maxRows": limit, "memoryMb": memory_mb(),
                                                   "preload": list(PRELOAD)}), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SANDBOX), str(work)], cwd=work, env=_environment(),
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout_seconds(), creationflags=flags)
        except subprocess.TimeoutExpired:
            raise ValueError(f"The script ran longer than {timeout_seconds()} s (ANALYTICS_PYTHON_TIMEOUT_SECONDS) and was stopped") from None
        report_file = work / "result.json"
        if not report_file.exists() or report_file.stat().st_size > 2_000_000:
            detail = process.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] if process.stderr else []
            if process.returncode and not detail:
                detail = [f"exit code {process.returncode}; it may have used more than {memory_mb()} MB of memory"]
            LOG.warning("Analytics script ended without a report: %s", " ".join(detail)[:300])
            return {"ok": False, "error": f"The script stopped without a result ({' '.join(detail)[:300] or 'no report'})",
                    "output": "", "notes": [], "columns": [], "rowCount": 0, "rows": [], "durationMs": _ms(started)}
        report = json.loads(report_file.read_text(encoding="utf-8"))
        result = {"ok": bool(report.get("ok")), "error": str(report.get("error") or "")[:4000],
                  "output": str(report.get("output") or "")[:70_000], "notes": [str(n)[:200] for n in report.get("notes") or []][:5],
                  "columns": [], "rowCount": 0, "rows": [], "durationMs": _ms(started)}
        produced = work / "out.ndjson"
        if result["ok"] and produced.exists():
            dates = [name for name, kind in (report.get("dtypes") or {}).items() if str(kind).startswith("datetime64")]
            result.update(_read_result(produced, output, preview_rows, dates))
        elif result["ok"]:
            result.update({"ok": False, "error": "The script did not leave a result"})
        result["durationMs"] = _ms(started)
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _read_result(produced: Path, output: Path | None, preview_rows: int, dates: list[str] | None = None) -> dict[str, Any]:
    """The script's NDJSON output read by DuckDB (types detected again: numbers, dates, text): the first rows and,
    when saving, a Parquet file."""
    from backend.app.analytics import importer  # noqa: PLC0415
    if produced.stat().st_size == 0:
        return {"columns": [], "rowCount": 0, "rows": []}
    source = (f"read_json({importer.quote_path(produced)}, format='newline_delimited', union_by_name=true, sample_size=-1, "
              "maximum_object_size=67108864)")
    con = importer.connect()
    known = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()}
    stamps = [name for name in dates or [] if name in known]
    if stamps:                                     # pandas dates arrive as ISO text: dates again
        quoted = lambda name: '"' + name.replace('"', '""') + '"'  # noqa: E731
        source = f"(SELECT * REPLACE ({', '.join(f'TRY_CAST({quoted(n)} AS TIMESTAMP) AS {quoted(n)}' for n in stamps)}) FROM {source})"
    try:
        count = int(con.execute(f"SELECT count(*) FROM {source}").fetchone()[0])
        cursor = con.execute(f"SELECT * FROM {source} LIMIT {int(preview_rows)}")
        columns = [item[0] for item in cursor.description]
        rows = [[importer.plain(value) for value in row] for row in cursor.fetchall()]
        if output is not None and count:
            part = output.with_suffix(".parquet.part")
            con.execute(f"COPY (SELECT * FROM {source}) TO {importer.quote_path(part)} (FORMAT parquet, COMPRESSION zstd)")
            part.replace(output)
    finally:
        con.close()
    return {"columns": columns, "rowCount": count, "rows": rows}


def run_import(dataset: dict[str, Any], find: Callable[[str], dict[str, Any] | None]) -> dict[str, Any]:
    """Refresh a Python dataset: run its script on the current data of its inputs (the importer's result shape)."""
    import secrets  # noqa: PLC0415

    from backend.app.analytics import importer  # noqa: PLC0415
    started = time.monotonic()
    inputs = []
    for dataset_id in dataset.get("inputs") or []:
        item = find(dataset_id)
        if item is None:
            raise ValueError(f"An input dataset ({dataset_id}) was deleted; choose the inputs again")
        inputs.append(item)
    name = f"{dataset['id']}-{secrets.token_hex(4)}.parquet"
    target = settings.directory() / name
    result = run(str(dataset.get("querySource") or ""), inputs, output=target, preview_rows=0)
    if not result["ok"]:
        raise ValueError(f"The script failed: {result['error'][:400]}")
    if not result["rowCount"]:
        return {"file": "", "rowCount": 0, "sizeBytes": 0, "columns": [], "durationMs": _ms(started)}
    columns = importer.merge_columns(importer.describe(target), dataset.get("columns") or [])
    return {"file": name, "rowCount": result["rowCount"], "sizeBytes": target.stat().st_size, "columns": columns,
            "durationMs": _ms(started)}
