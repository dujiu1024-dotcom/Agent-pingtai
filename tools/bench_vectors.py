#!/usr/bin/env python3
"""向量检索基准：合成 N 条向量，测冷/热查询 P95。

  python3 tools/bench_vectors.py --n 10000 --dim 256 --queries 20
"""

import argparse
import array
import json
import os
import random
import statistics
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--dim", type=int, default=256)
    ap.add_argument("--queries", type=int, default=20)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    os.environ["AGENCY_DB"] = tempfile.mktemp(suffix=".db")
    import db as dbm
    import vectors

    with closing(dbm.connect()) as con, con:
        dbm.migrate(con)
        rng = random.Random(42)
        payload = []
        for i in range(args.n):
            vec = [rng.uniform(-1, 1) for _ in range(args.dim)]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vec = [v / norm for v in vec]
            payload.append((
                f"doc-{i}", 0, "占位文本", f"h{i}", vectors.EMBED_MODEL,
                args.dim, array.array("f", vec).tobytes(), "active", dbm.now()))
        con.executemany(
            "INSERT INTO embeddings(source_type,source_id,chunk_no,text,"
            "content_hash,model,dim,vector,state,created_at)"
            " VALUES('case',?,?,?,?,?,?,?,?,?)", payload)

    query = [rng.uniform(-1, 1) for _ in range(args.dim)]
    vectors._ollama_embed = lambda texts, timeout=20: [query]

    times = []
    for _ in range(args.queries):
        start = time.perf_counter()
        hits, reason = vectors._search_dense("bench", None, 8, 0.0)
        times.append((time.perf_counter() - start) * 1000)
    first = times[0]
    p95 = percentile(times, 95)
    result = {
        "label": args.label,
        "n": args.n,
        "dim": args.dim,
        "queries": args.queries,
        "cold_ms": round(first, 1),
        "p50_ms": round(statistics.median(times), 1),
        "p95_ms": round(p95, 1),
        "hits": len(hits),
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
