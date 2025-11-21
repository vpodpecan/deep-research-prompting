#!/usr/bin/env python
import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime

from openai import OpenAI


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

def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT UNIQUE,
        status TEXT,
        created_at TEXT,
        completed_at TEXT,
        input_file_id TEXT,
        output_file_id TEXT,
        jsonl_path TEXT,
        total_prompts INTEGER,
        model TEXT,
        instructions TEXT,
        max_tool_calls INTEGER,
        notes TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT,
        custom_id TEXT,
        prompt TEXT,
        response TEXT,
        error TEXT,
        created_at TEXT,
        finished_at TEXT
    )
    """)

    conn.commit()
    return conn


# ============================================================
# Prompt loading
# ============================================================

def load_prompts(folder: str):
    prompts = {}
    for fname in os.listdir(folder):
        if fname.lower().endswith(".txt"):
            path = os.path.join(folder, fname)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
                if not text:
                    continue
                custom_id = os.path.splitext(fname)[0]
                prompts[custom_id] = text
    return prompts


# ============================================================
# JSONL building + validation
# ============================================================

def validate_entry(entry: dict):
    if not isinstance(entry.get("custom_id"), str) or not entry["custom_id"].strip():
        raise ValueError("custom_id must be a non-empty string")

    if entry.get("method") != "POST":
        raise ValueError("method must be POST")

    if entry.get("url") != "/v1/responses":
        raise ValueError("url must be /v1/responses")

    body = entry.get("body")
    if not isinstance(body, dict):
        raise ValueError("body must be an object")

    if "model" not in body or not isinstance(body["model"], str) or not body["model"].strip():
        raise ValueError("body.model must be a non-empty string")

    if "input" not in body or not isinstance(body["input"], str) or not body["input"].strip():
        raise ValueError("body.input must be a non-empty string")

    mt = body.get("max_tool_calls")
    if not isinstance(mt, int) or mt < 1:
        raise ValueError("max_tool_calls must be an integer >= 1")

    tools = body.get("tools")
    if not isinstance(tools, list) or not tools:
        raise ValueError("tools must be a non-empty list")

    for t in tools:
        if not isinstance(t, dict) or "type" not in t:
            raise ValueError("each tool must be an object with a 'type' field")

    # Check JSON serializability
    json.dumps(entry)


def build_jsonl(prompts: dict, model: str, max_tool_calls: int, instructions: str,
                tmp_dir: str = "tmp") -> str:
    os.makedirs(tmp_dir, exist_ok=True)
    filename = f"batch_input_{uuid.uuid4().hex}.jsonl"
    full_path = os.path.join(tmp_dir, filename)

    with open(full_path, "w", encoding="utf-8") as out:
        for custom_id, text in prompts.items():
            entry = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/responses",
                "body": {
                    "model": model,
                    "instructions": instructions,
                    "input": text,
                    "max_tool_calls": max_tool_calls,
                    "tools": [
                        {"type": "web_search"}
                    ]
                }
            }
            validate_entry(entry)
            out.write(json.dumps(entry) + "\n")

    return full_path


# ============================================================
# Batch submission + DB persistence
# ============================================================

def record_batch_and_prompts(conn, batch, input_file_id: str, jsonl_path: str,
                             prompts: dict, model: str,
                             instructions: str, max_tool_calls: int):
    cur = conn.cursor()

    batch_id = batch.id
    status = getattr(batch, "status", None)
    created_at = getattr(batch, "created_at", None)
    completed_at = getattr(batch, "completed_at", None)
    output_file_id = getattr(batch, "output_file_id", None)

    cur.execute("""
        INSERT OR REPLACE INTO batches
        (batch_id, status, created_at, completed_at, input_file_id, output_file_id,
         jsonl_path, total_prompts, model, instructions, max_tool_calls, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        batch_id,
        status,
        created_at,
        completed_at,
        input_file_id,
        output_file_id,
        jsonl_path,
        len(prompts),
        model,
        instructions,
        max_tool_calls,
        None
    ))

    # Insert placeholder rows for responses (prompt known, response not yet)
    for custom_id, prompt_text in prompts.items():
        cur.execute("""
            INSERT INTO responses
            (batch_id, custom_id, prompt, response, error, created_at, finished_at)
            VALUES (?, ?, ?, NULL, NULL, NULL, NULL)
        """, (batch_id, custom_id, prompt_text))

    conn.commit()


def submit_batch(jsonl_path: str, api_key: str):
    client = OpenAI(api_key=api_key)

    with open(jsonl_path, "rb") as f:
        input_file = client.files.create(file=f, purpose="batch")

    batch = client.batches.create(
        input_file_id=input_file.id,
        endpoint="/v1/responses",
        completion_window="24h"
    )

    return batch, input_file


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Submit prompts via OpenAI Batch API and store batch metadata in SQLite."
    )
    parser.add_argument("input_folder", help="Folder containing .txt prompt files")
    parser.add_argument("db_path", help="SQLite database path")
    parser.add_argument(
        "--model",
        default="o4-mini-deep-research",
        help="Deep Research model (default: o4-mini-deep-research)",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=10,
        help="max_tool_calls for Deep Research (default: 10)",
    )
    parser.add_argument(
        "--instructions",
        default=(
            "You are an expert who provides concise, scientifically reliable information "
            "based strictly on professional and peer-reviewed sources."
        ),
        help="System-level instructions for Deep Research."
    )
    parser.add_argument(
        "--tmp-dir",
        default="tmp",
        help="Temporary directory for JSONL files (default: tmp)",
    )
    parser.add_argument("--keyfile", default="api_keys/openai_api_key_legumeES-DR",
                        help="File containing the OpenAI API key")


    args = parser.parse_args()

    conn = init_db(args.db_path)

    api_key = read_api_key(args.keyfile)


    prompts = load_prompts(args.input_folder)
    if not prompts:
        print("No non-empty .txt prompts found in input folder.")
        return

    jsonl_path = build_jsonl(prompts, args.model, args.max_tool_calls,
                             args.instructions, tmp_dir=args.tmp_dir)
    print(f"[{datetime.now().isoformat()}] JSONL built: {jsonl_path}")

    batch, input_file = submit_batch(jsonl_path, api_key)
    print(f"[{datetime.now().isoformat()}] Batch submitted: {batch.id}, status={batch.status}")

    record_batch_and_prompts(
        conn,
        batch,
        input_file_id=input_file.id,
        jsonl_path=jsonl_path,
        prompts=prompts,
        model=args.model,
        instructions=args.instructions,
        max_tool_calls=args.max_tool_calls,
    )

    print("Batch metadata and prompts stored in DB. You can now run poll_batches.py to monitor and fetch results.")


if __name__ == "__main__":
    main()
