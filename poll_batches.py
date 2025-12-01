#!/usr/bin/env python
import argparse
import json
import sqlite3
import time
from datetime import datetime
import sys
import signal

from openai import OpenAI


def signal_handler(sig, frame):
    print('Interrupt requested, polling stopped.')
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)


def read_api_key(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"ERROR: Failed to read API key from {path}: {e}", file=sys.stderr)
        sys.exit(1)


# ============================================================
# DB helpers
# ============================================================

def connect_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_pending_batches(conn, batch_id: str | None = None):
    cur = conn.cursor()
    if batch_id:
        cur.execute("""
            SELECT * FROM batches
            WHERE batch_id = ?
              AND (status IS NULL OR status NOT IN ('completed','failed','cancelled'))
        """, (batch_id,))
    else:
        cur.execute("""
            SELECT * FROM batches
            WHERE status IS NULL OR status NOT IN ('completed','failed','cancelled')
        """)
    return cur.fetchall()


def get_batches_for_force(conn, batch_id: str | None = None):
    """
    In force-download mode:
      - If batch_id is given: operate on that batch (whatever its status)
      - Else: operate on all completed batches
    """
    cur = conn.cursor()
    if batch_id:
        cur.execute("""
            SELECT * FROM batches
            WHERE batch_id = ?
        """, (batch_id,))
    else:
        cur.execute("""
            SELECT * FROM batches
            WHERE status = 'completed'
        """)
    return cur.fetchall()


def update_batch_metadata(conn, batch_row, batch):
    cur = conn.cursor()
    cur.execute("""
        UPDATE batches
        SET status = ?,
            created_at = ?,
            completed_at = ?,
            input_file_id = COALESCE(?, input_file_id),
            output_file_id = COALESCE(?, output_file_id)
        WHERE batch_id = ?
    """, (
        getattr(batch, "status", None),
        getattr(batch, "created_at", None),
        getattr(batch, "completed_at", None),
        getattr(batch, "input_file_id", batch_row["input_file_id"]),
        getattr(batch, "output_file_id", batch_row["output_file_id"]),
        batch.id
    ))
    conn.commit()


def store_output_for_batch(conn, batch_id: str, output_jsonl: str):
    cur = conn.cursor()

    for line in output_jsonl.splitlines():
        if not line.strip():
            continue

        obj = json.loads(line)
        custom_id = obj.get("custom_id")
        error_obj = obj.get("error")
        body = obj.get("response", {})

        if error_obj:
            response_json = None
            error_json = json.dumps(error_obj)
            created = None
            finished = None
        else:
            response_json = json.dumps(body)
            error_json = None
            metadata = body.get("metadata", {})
            created = metadata.get("created")
            finished = metadata.get("finished")

        cur.execute("""
            UPDATE responses
            SET response = ?,
                error = ?,
                created_at = ?,
                finished_at = ?
            WHERE batch_id = ? AND custom_id = ?
        """, (response_json, error_json, created, finished, batch_id, custom_id))

    conn.commit()


# ============================================================
# Polling logic
# ============================================================

def poll_batches(db_path: str, poll_interval: int, api_key: str,
                 batch_id_filter: str | None = None,
                 force_download: bool = False):
    conn = connect_db(db_path)
    client = OpenAI(api_key=api_key)

    # --------------------------------------------------------
    # FORCE MODE: one-shot re-download for completed batches
    # --------------------------------------------------------
    if force_download:
        rows = get_batches_for_force(conn, batch_id_filter)
        if not rows:
            print(f"[{datetime.now().isoformat()}] No batches found matching criteria for force-download.")
            return

        for row in rows:
            b_id = row["batch_id"]
            print(f"[{datetime.now().isoformat()}] Force processing batch {b_id}...")

            try:
                batch = client.batches.retrieve(b_id)
            except Exception as e:
                print(f"  Error retrieving batch {b_id}: {e}")
                continue

            status = getattr(batch, "status", None)
            print(f"  Status: {status}")

            update_batch_metadata(conn, row, batch)

            if status != "completed":
                print(f"  Batch {b_id} is not completed (status={status}), cannot download output.")
                continue

            output_file_id = getattr(batch, "output_file_id", None)
            if not output_file_id:
                print(f"  Batch {b_id} completed but has no output_file_id.")
                continue

            # Clear any existing outputs for this batch
            cur = conn.cursor()
            cur.execute("""
                UPDATE responses
                SET response = NULL,
                    error = NULL,
                    created_at = NULL,
                    finished_at = NULL
                WHERE batch_id = ?
            """, (b_id,))
            conn.commit()

            # Download fresh output
            response = client.files.content(output_file_id)
            output_jsonl = response.text

            store_output_for_batch(conn, b_id, output_jsonl)
            print(f"  Force-downloaded and stored responses for batch {b_id}.")

        print(f"[{datetime.now().isoformat()}] Force-download run finished.")
        return

    # --------------------------------------------------------
    # NORMAL MODE: poll pending batches until nothing left
    # --------------------------------------------------------
    while True:
        rows = get_pending_batches(conn, batch_id_filter)
        if not rows:
            print(f"[{datetime.now().isoformat()}] No pending batches. Exiting.")
            break

        any_pending = False

        for row in rows:
            b_id = row["batch_id"]
            print(f"[{datetime.now().isoformat()}] Checking batch {b_id}...")

            try:
                batch = client.batches.retrieve(b_id)
            except Exception as e:
                print(f"  Error retrieving batch {b_id}: {e}")
                any_pending = True
                continue

            status = getattr(batch, "status", None)
            print(f"  Status: {status}")

            update_batch_metadata(conn, row, batch)

            if status == "completed":
                output_file_id = getattr(batch, "output_file_id", None)
                if not output_file_id:
                    print(f"  Batch {b_id} completed but has no output_file_id.")
                    continue

                # Check if we already have responses (response or error present)
                cur = conn.cursor()
                cur.execute("""
                    SELECT COUNT(*) FROM responses
                    WHERE batch_id = ? AND (response IS NOT NULL OR error IS NOT NULL)
                """, (b_id,))
                existing_count = cur.fetchone()[0]

                if existing_count > 0:
                    print(f"  Responses already stored ({existing_count}), skipping download.")
                    continue

                try:
                    response = client.files.content(output_file_id)
                except Exception as e:
                    print(f"  Error downloading output file for {b_id}: {e}")
                    continue

                output_jsonl = response.text

                store_output_for_batch(conn, b_id, output_jsonl)
                print(f"  Stored responses for batch {b_id}.")

            elif status in ("failed", "cancelled"):
                print(f"  Batch {b_id} ended with status: {status}")
            else:
                any_pending = True

        if not any_pending:
            print(f"[{datetime.now().isoformat()}] No more in-progress batches. Exiting.")
            break

        print(f"[{datetime.now().isoformat()}] Sleeping {poll_interval}s before next check...")
        time.sleep(poll_interval)


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Poll OpenAI batches and save results to the database."
    )
    parser.add_argument("db_path", help="SQLite DB file")
    parser.add_argument("--poll-interval", type=int, default=15,
                        help="Seconds between polling cycles (default: 15)")
    parser.add_argument("--batch-id", default=None,
                        help="Only poll or force-download this specific batch ID")
    parser.add_argument("--force-download", action="store_true",
                        help="One-shot mode: re-download outputs and overwrite DB for completed batches")
    parser.add_argument("--keyfile", default="api_keys/openai_api_key_deep_research",
                        help="File containing the OpenAI API key")

    args = parser.parse_args()
    api_key = read_api_key(args.keyfile)

    poll_batches(
        args.db_path,
        args.poll_interval,
        api_key,
        batch_id_filter=args.batch_id,
        force_download=args.force_download
    )


if __name__ == "__main__":
    main()
