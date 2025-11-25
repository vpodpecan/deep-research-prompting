#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

# Usage:
#   ./run_all.sh input_folder output_folder
#   ./run_all.sh               # defaults: . and ./out

indir="${1:-.}"
outdir="${2:-./out}"

# Normalize paths (remove trailing /)
indir="${indir%/}"
outdir="${outdir%/}"

# Check input folder exists
if [[ ! -d "$indir" ]]; then
    echo "Input folder '$indir' does not exist." >&2
    exit 1
fi

# Create output folder if needed
mkdir -p "$outdir"

declare -A seen_codes

# Loop over all docx files that begin with two digits and a dash in the input folder
for f in "$indir"/??-*.docx; do
    # Get just the filename (without folder path)
    filename=${f##*/}          # e.g. "01-Lucerne (Alfalfa)-13-Fixed nitrogen.docx"

    # Extract leading code (e.g. "01")
    code=${filename%%-*}

    # Skip if we've already processed this code
    if [[ -n "${seen_codes[$code]:-}" ]]; then
        continue
    fi
    seen_codes[$code]=1

    # Extract crop name:
    # filename: 01-Lucerne (Alfalfa)-13-Fixed nitrogen.docx
    # 1) strip first chunk + '-' -> "Lucerne (Alfalfa)-13-Fixed nitrogen.docx"
    rest=${filename#*-}
    # 2) take up to next '-'    -> "Lucerne (Alfalfa)"
    crop=${rest%%-*}

    echo "Processing code $code in '$indir' -> crop '$crop'"

    # Collect all matching files for this code and sort them alphabetically
    mapfile -t files < <(printf '%s\n' "$indir"/"$code"-*.docx | sort)

    # Safety: skip if somehow no files found
    [[ ${#files[@]} -eq 0 ]] && continue

    # Call python with the sorted list, output into the output folder
    python merge_docx.py "${files[@]}" -o "$outdir/${crop}.docx"
done
