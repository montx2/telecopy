"""Soak simulado de 100k itens: checkpoints/intenções/dedup, sem rede real."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import Repository  # noqa: E402


def main(total: int = 100_000) -> int:
    tracemalloc.start()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        repo = Repository(Path(tmp) / "soak.db")
        try:
            with repo._db() as conn:  # noqa: SLF001
                conn.execute("BEGIN")
                for i in range(1, total + 1):
                    conn.execute(
                        "INSERT INTO checkpoints(source_id,dest_id,msg_id,status,dest_msg_id,timestamp) "
                        "VALUES(?,?,?,?,?,?)", (-1, -2, i, "done", 1000 + i, time.time()),
                    )
                    conn.execute(
                        "INSERT INTO send_intents(source_id,dest_id,msg_id,part_index,state,fingerprint,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", (-1, -2, i, 0, "confirmed", f"fp{i}", time.time(), time.time()),
                    )
                    if i % 10 == 0:
                        conn.execute(
                            "INSERT OR IGNORE INTO dedup(content_hash,dest_id,sent_msg_id,created_at) VALUES(?,?,?,?)",
                            (f"h{i}", -2, 1000 + i, time.time()),
                        )
                conn.execute("COMMIT")
            assert len(repo.done_ids(-1, -2)) == total
            health = repo.health()
            assert health["integrity"] == "ok"
        finally:
            repo.close()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result = {
        "tipo": "rede simulada / banco local", "itens": total,
        "segundos": round(time.perf_counter() - started, 3),
        "pico_memoria_mib": round(peak / 1048576, 2),
        "integridade": "ok",
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000))
