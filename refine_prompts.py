#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
from openai import OpenAI

def read_api_key(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"ERROR: Failed to read API key from {path}: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Submit prompts to OpenAI and save responses.")
    parser.add_argument("input_folder", help="Folder with input .txt prompt files")
    parser.add_argument("output_folder", help="Folder to store output .txt files")
    parser.add_argument("--model", default="gpt-4.1", help="OpenAI model to use (default: gpt-4.1)")
    parser.add_argument("--keyfile", default="api_keys/openai_api_key_deep_research",
                        help="File containing the OpenAI API key")
    args = parser.parse_args()

    input_dir = Path(args.input_folder)
    output_dir = Path(args.output_folder)
    key_path = Path(args.keyfile)

    if not input_dir.exists():
        print(f"ERROR: Input folder {input_dir} does not exist.", file=sys.stderr)
        sys.exit(1)
    if not output_dir.exists():
        output_dir.mkdir(parents=True)

    # Read API key
    api_key = read_api_key(key_path)

    # Init client
    client = OpenAI(api_key=api_key)

    # Determine which files need processing
    input_files = sorted([p for p in input_dir.glob("*.txt")])
    output_files = {p.name for p in output_dir.glob("*.txt")}

    files_to_process = [p for p in input_files if p.name not in output_files]

    if not files_to_process:
        print("All input files already processed. Nothing to do.")
        return

    print(f"Found {len(files_to_process)} new files to process.")

    # Log file for failures
    fail_log = output_dir / "failed.log"

    for in_file in files_to_process:
        print(f"Processing: {in_file.name}")

        try:
            with open(in_file, "r", encoding="utf-8") as f:
                prompt_text = f.read()

            # Submit to OpenAI Responses API
            response = client.responses.create(
                model=args.model,
                input=prompt_text,
                # temperature=0
                top_p=0.1
            )

            # Extract output text
            out_text = response.output_text

            # Save output
            out_path = output_dir / in_file.name
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(out_text)

        except Exception as e:
            err_msg = f"{in_file.name}: {e}"
            print("ERROR:", err_msg, file=sys.stderr)
            with open(fail_log, "a", encoding="utf-8") as flog:
                flog.write(err_msg + "\n")
            continue

    print("Done.")

if __name__ == "__main__":
    main()
