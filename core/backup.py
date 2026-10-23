"""Backups of everything the agents and you have written: the database, settings, positions, account
and the Strategy Lab workspace. Stored in backups/<YYYY-MM-DD_HHMMSS>_<tag>/ next to the project.

    backup("daily")   once per day, automatically before the agents run (skipped if one exists today)
    backup("manual")  from Settings -> Backups
    restore(name)     takes a 'pre-restore' backup first, then copies the files back
Keeps the newest KEEP backups."""
import shutil, time
from pathlib import Path
from .config import BASE, DB_PATH, SETTINGS_JSON, POSITIONS_CSV, ACCOUNT_JSON

DIR = BASE / "backups"
KEEP = 20
LAB_DB = BASE / "lab" / "workspace" / "lab.duckdb"
LAB_STRATS = BASE / "lab" / "workspace" / "strategies"
FILES = [DB_PATH, SETTINGS_JSON, POSITIONS_CSV, ACCOUNT_JSON, BASE / "data" / "corporate_actions.csv",
         BASE / "data" / "quality_reviewed.csv", LAB_DB, BASE / "data" / "announcements.parquet",
         BASE / "config" / "screener" / "enabled.json", BASE / "data" / "news" / "news.parquet",
         BASE / "data" / "news_aliases.csv", BASE / "data" / "company_info.csv",
         # things you type in (can't be downloaded again)
         BASE / "data" / "macro" / "india.csv", BASE / "data" / "macro" / "calendar.csv", BASE / "data" / "search" / "exposures.csv",
         BASE / "data" / "search" / "web_options.json", BASE / "data" / "news" / "options.json", BASE / "data" / "library" / "sources.parquet",
         BASE / "data" / "library" / "options.json",
         # histories that only build up over time (GDELT keeps 3 months, FII/DII is daily)
         BASE / "data" / "macro" / "series.parquet", BASE / "data" / "announcements_backfill.json"]


def _checkpoint(path):
    """Flush DuckDB's write-ahead log into the main file so a plain copy is complete."""
    if not path.exists():
        return
    try:
        import duckdb
        con = duckdb.connect(str(path))
        con.execute("checkpoint")
        con.close()
    except Exception:
        pass   # busy (an agent is writing): copy whatever is there, including the .wal file


def list_backups():
    if not DIR.exists():
        return []
    out = []
    for d in sorted((p for p in DIR.iterdir() if p.is_dir()), reverse=True):
        size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
        out.append(dict(name=d.name, tag=d.name.split("_", 2)[-1], when=time.strftime("%Y-%m-%d %H:%M", time.localtime(d.stat().st_mtime)),
                        size_mb=round(size / 1e6, 2), files=sum(1 for f in d.rglob("*") if f.is_file())))
    return out


def backup(tag="manual"):
    """Returns the backup folder name, or None if a daily backup already exists today."""
    today = time.strftime("%Y-%m-%d")
    if tag == "daily" and any(b["name"].startswith(today) and b["tag"] == "daily" for b in list_backups()):
        return None
    dest = DIR / f"{time.strftime('%Y-%m-%d_%H%M%S')}_{tag}"
    dest.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        if f.suffix == ".duckdb":
            _checkpoint(f)
        for src in (f, f.with_name(f.name + ".wal")):
            if src.exists():
                rel = src.relative_to(BASE)
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest / rel)
    if LAB_STRATS.exists():
        shutil.copytree(LAB_STRATS, dest / LAB_STRATS.relative_to(BASE), dirs_exist_ok=True)
    _prune()
    return dest.name


def _prune():
    bs = list_backups()
    for b in bs[KEEP:]:
        if b["tag"] == "pre-restore" and b is bs[KEEP]:
            continue
        shutil.rmtree(DIR / b["name"], ignore_errors=True)


def restore(name):
    src = DIR / name
    if not src.is_dir():
        raise ValueError(f"backup {name} not found")
    from . import jobs
    if jobs.current():
        raise RuntimeError("A job is running. Stop it or wait before restoring.")
    safety = backup("pre-restore")
    for f in src.rglob("*"):
        if f.is_file():
            dst = BASE / f.relative_to(src)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    # a .wal that was not in the backup belongs to the replaced database: remove it
    for db in (DB_PATH, LAB_DB):
        wal = db.with_name(db.name + ".wal")
        if wal.exists() and not (src / wal.relative_to(BASE)).exists():
            wal.unlink()
    return safety


if __name__ == "__main__":
    import sys
    print(backup(sys.argv[1] if len(sys.argv) > 1 else "manual") or "daily backup already exists today")
