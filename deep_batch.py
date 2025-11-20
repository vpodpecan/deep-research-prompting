import argparse
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime

from openai import OpenAI


# ------------------------------------------------------------
# SQLite Setup
# ------------------------------------------------------------
def init_db(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT,
        created_at TEXT,
        completed_at TEXT,
        num_requests INTEGER,
        input_file TEXT,
        output_file TEXT,
        status TEXT,
        raw_metadata TEXT
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


# ------------------------------------------------------------
# Read prompt text files
# ------------------------------------------------------------
def load_prompts(folder):
    prompts = {}
    for fname in os.listdir(folder):
        if fname.lower().endswith(".txt"):
            with open(os.path.join(folder, fname), "r", encoding="utf-8") as f:
                text = f.read().strip()
                if text:
                    prompts[os.path.splitext(fname)[0]] = text
                else:
                    print(f'Ignoring empty prompt file {fname}')
    return prompts


def validate_entry(entry):
    # Basic required fields
    if not isinstance(entry.get("custom_id"), str) or not entry["custom_id"].strip():
        raise ValueError("custom_id must be a non-empty string")

    if entry.get("method") != "POST":
        raise ValueError("method must be POST")

    if entry.get("url") != "/v1/responses":
        raise ValueError("url must be /v1/responses")

    body = entry.get("body")
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    # Body contents
    if "model" not in body or not isinstance(body["model"], str) or not body["model"].strip():
        raise ValueError("body.model must be a non-empty string")

    if "input" not in body or not isinstance(body["input"], str) or not body["input"].strip():
        raise ValueError("body.input must be a non-empty prompt string")

    mt = body.get("max_tool_calls")
    if not isinstance(mt, int) or mt < 1:
        raise ValueError("max_tool_calls must be an integer >= 1")

    # Tool validation
    tools = body.get("tools")
    if not isinstance(tools, list) or len(tools) == 0:
        raise ValueError("tools must be a non-empty list")

    for t in tools:
        if not isinstance(t, dict) or "type" not in t:
            raise ValueError("each tool must be an object with a 'type' field")

    # Check JSON-serializable
    try:
        json.dumps(entry)
    except Exception as e:
        raise ValueError(f"Final JSON entry is not serializable: {e}")


def build_jsonl(prompts, model, max_tool_calls, instructions, tmp_dir="tmp"):
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

            # Validate before writing
            validate_entry(entry)

            # Write JSONL line
            out.write(json.dumps(entry) + "\n")

    return full_path


# ------------------------------------------------------------
# Submit batch job
# ------------------------------------------------------------
def submit_batch(client, jsonl_path):
    with open(jsonl_path, "rb") as f:
        input_file = client.files.create(file=f, purpose="batch")

    batch = client.batches.create(
        input_file_id=input_file.id,
        endpoint="/v1/responses",
        completion_window="24h"
    )
    return batch


# ------------------------------------------------------------
# Poll until batch completes
# ------------------------------------------------------------
def poll_batch(client, batch_id, interval):
    while True:
        batch = client.batches.retrieve(batch_id)
        status = batch.status
        print(f"[{datetime.now().isoformat()}] Batch {batch_id} status: {status}")

        if status in ("completed", "failed", "cancelled"):
            return batch

        time.sleep(interval)


# ------------------------------------------------------------
# Download output JSONL
# ------------------------------------------------------------
def download_output(client, file_id):
    response = client.files.content(file_id)
    return response.text


# ------------------------------------------------------------
# Store metadata + responses in SQLite
# ------------------------------------------------------------
def store_results(conn, batch, prompts, output_jsonl):
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO batches
        (batch_id, created_at, completed_at, num_requests, input_file, output_file, status, raw_metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        batch.id,
        batch.created_at,
        batch.completed_at,
        len(prompts),
        batch.input_file_id,
        batch.output_file_id,
        batch.status,
        json.dumps(batch.to_dict())
    ))
    conn.commit()

    for line in output_jsonl.splitlines():
        if not line.strip():
            continue

        obj = json.loads(line)
        cid = obj.get("custom_id")

        prompt_text = prompts.get(cid, "")

        error = obj.get("error")
        if error:
            response_text = None
            error_text = json.dumps(error)
            created = None
            finished = None
        else:
            body = obj.get("response", {})
            response_text = json.dumps(body)
            error_text = None
            meta = body.get("metadata", {})
            created = meta.get("created")
            finished = meta.get("finished")

        cur.execute("""
            INSERT INTO responses
            (batch_id, custom_id, prompt, response, error, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            batch.id,
            cid,
            prompt_text,
            response_text,
            error_text,
            created,
            finished
        ))

    conn.commit()


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Run deep-research prompts via OpenAI Batch API")
    parser.add_argument("input_folder", help="Folder containing prompt text files")
    parser.add_argument("output_db", help="SQLite database path")
    parser.add_argument("poll_interval", type=int, help="Seconds between polling")

    parser.add_argument("--model", default="o4-mini-deep-research",
                        help="Deep Research model name (default: o4-mini-deep-research)")

    parser.add_argument("--max-tool-calls", type=int, default=5,
                        help="max_tool_calls parameter for deep research (default: 5)")

    parser.add_argument(
        "--instructions",
        default="You are an expert who provides concise, scientifically reliable information based strictly on professional and peer-reviewed sources.",
        help="System-level instructions for Deep Research (default: expert scientific mode)."
    )


    args = parser.parse_args()

    conn = init_db(args.output_db)

    prompts = load_prompts(args.input_folder)
    if not prompts:
        print("No .txt prompt files found in folder.")
        return

    jsonl_path = build_jsonl(prompts, args.model, args.max_tool_calls, args.instructions)

    client = OpenAI()

    batch = submit_batch(client, jsonl_path)
    print(f"Batch submitted: {batch.id}")

    batch = poll_batch(client, batch.id, args.poll_interval)

    if batch.status != "completed":
        print(f"Batch ended with status: {batch.status}")
        store_results(conn, batch, prompts, "")
        return

    output_jsonl = download_output(client, batch.output_file_id)
    store_results(conn, batch, prompts, output_jsonl)

    print("All results stored in SQLite.")


if __name__ == "__main__":
    main()
