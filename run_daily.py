"""Whole daily pipeline: regime -> technical -> risk -> validation -> report.
    python3 run_daily.py        (same as: python3 pipeline.py all)"""
import pipeline

if __name__ == "__main__":
    raise SystemExit(pipeline.run(pipeline.STEPS))
