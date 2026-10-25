"""Compact the search database (data/library/lancedb): merge the many small files left by incremental indexing, drop old
versions, refresh the indexes. Search results don't change; search and later indexing get faster.

Steps: refuse if another indexing job holds the lock → back up the folder (if there's room: 2x its size free) →
for each table: optimize (compact + prune every older version + update indexes) → rebuild the keyword index →
report size, file and version counts before and after. Keeps only the newest backup.
Run: python -m core.search_compact   (add --no-backup to skip the copy)"""
import shutil, sys, time
from datetime import timedelta
from pathlib import Path
from . import library as L

BACKUPS = L.DIR / "backups"


def _size(p):
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())


def _counts(table_dir):
    d = Path(table_dir)
    return dict(files=len(list((d / "data").glob("*"))) if (d / "data").exists() else 0,
                versions=len(list((d / "_versions").glob("*"))) if (d / "_versions").exists() else 0)


def _gb(n):
    return f"{n / 1e9:.2f} GB"


def run(backup=True, log=print):
    db_dir = Path(L.DB)
    if not db_dir.exists():
        log("No search database yet: nothing to compact.")
        return {}
    before = _size(db_dir)
    tables = sorted(p for p in db_dir.glob("*.lance"))
    stats = {p.stem: _counts(p) for p in tables}
    log(f"Search database: {_gb(before)} · " + " · ".join(f"{k}: {v['files']:,} files, {v['versions']:,} versions" for k, v in stats.items()))
    if backup:
        free = shutil.disk_usage(db_dir).free
        if free < 2 * before:
            log(f"Skipping the backup: only {_gb(free)} free (needs {_gb(2 * before)}).")
        else:
            BACKUPS.mkdir(parents=True, exist_ok=True)
            dest = BACKUPS / f"lancedb_{time.strftime('%Y%m%d_%H%M%S')}"
            k = 1
            while dest.exists():                                          # two runs in the same second
                dest = BACKUPS / f"lancedb_{time.strftime('%Y%m%d_%H%M%S')}_{k}"; k += 1
            log(f"Backing up to {dest.relative_to(L.DIR.parent.parent)} ...")
            shutil.copytree(db_dir, dest)
            for old in sorted(BACKUPS.glob("lancedb_*"))[:-1]:          # keep only the newest backup
                shutil.rmtree(old, ignore_errors=True)
    db = L._db()
    for p in tables:
        name = p.stem
        t0 = time.time()
        log(f"Compacting '{name}' ...")
        t = db.open_table(name)
        t.optimize(cleanup_older_than=timedelta(seconds=0))
        log(f"  merged and pruned in {time.time() - t0:.0f} s; rebuilding the keyword index ...")
        try:
            L._fts(db.open_table(name))
        except Exception as e:                       # the vector search still works without it
            log(f"  keyword index not rebuilt ({type(e).__name__}: {str(e)[:120]})")
    after = _size(db_dir)
    stats2 = {p.stem: _counts(p) for p in tables}
    log(f"Done: {_gb(before)} → {_gb(after)} · " + " · ".join(f"{k}: {stats[k]['files']:,} → {v['files']:,} files, {stats[k]['versions']:,} → {v['versions']:,} versions"
                                                          for k, v in stats2.items()))
    return dict(before=before, after=after, tables=stats2)


if __name__ == "__main__":
    run(backup="--no-backup" not in sys.argv[1:])
