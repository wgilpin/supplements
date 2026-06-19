"""Spike: verify Kùzu API shape + MERGE dedup (spec §8.1, §8.4)."""

import shutil
import tempfile
from pathlib import Path

import kuzu

tmp = Path(tempfile.mkdtemp()) / "kg.kuzu"
db = kuzu.Database(str(tmp))
conn = kuzu.Connection(db)

conn.execute("CREATE NODE TABLE Compound (name STRING, PRIMARY KEY (name))")

# MERGE the same compound twice -> expect exactly one row.
conn.execute('MERGE (c:Compound {name: "taurine"})')
conn.execute('MERGE (c:Compound {name: "taurine"})')

df = conn.execute("MATCH (c:Compound) RETURN c.name AS name").get_as_df()
print("kuzu version:", kuzu.__version__)
print("rows after 2x MERGE:", len(df))
print(df)
assert len(df) == 1, "MERGE did not dedup"
print("OK: MERGE dedups, get_as_df works")

shutil.rmtree(tmp.parent, ignore_errors=True)
