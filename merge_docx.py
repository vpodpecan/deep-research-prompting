#!/usr/bin/env python3
import argparse
import os
import sys
from copy import deepcopy
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def make_page_break():
    """Return <w:p><w:r><w:br w:type='page'/></w:r></w:p>"""
    p = OxmlElement("w:p")
    r = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    r.append(br)
    p.append(r)
    return p


def make_heading(text):
    """
    Create a proper Word Heading 1 paragraph:
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>text</w:t></w:r></w:p>
    """
    p = OxmlElement("w:p")

    # paragraph properties
    pPr = OxmlElement("w:pPr")
    pStyle = OxmlElement("w:pStyle")
    pStyle.set(qn("w:val"), "Heading1")
    pPr.append(pStyle)
    p.append(pPr)

    # run + text
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    p.append(r)

    return p


def merge_documents(files, output_file):
    # Filter .docx files
    docx_files = [f for f in files if f.lower().endswith(".docx") and os.path.isfile(f)]
    if not docx_files:
        print("No .docx files found.", file=sys.stderr)
        sys.exit(1)

    # Sort alphabetically
    docx_files = sorted(docx_files, key=lambda x: x.lower())

    print("Merging documents in this order:")
    for f in docx_files:
        print(" -", f)

    # Create blank master document
    master = Document()
    body = master._element.body

    # Remove default empty paragraph if present
    while len(body) > 0:
        del body[0]

    for idx, path in enumerate(docx_files):
        fname = os.path.basename(path)
        print(f"Appending: {fname}")

        # Add page break before each file except the first
        if idx > 0:
            body.append(make_page_break())

        # Add filename heading
        body.append(make_heading(fname))

        # Load source document
        src = Document(path)
        src_body = src._element.body

        # Append deep-copied XML blocks (preserves hyperlinks!)
        for child in src_body:
            body.append(deepcopy(child))

    master.save(output_file)
    print(f"\nMerged document saved as: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Merge .docx files into one, preserving hyperlinks and order."
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Input .docx files (shell globs like ABC*.docx are supported)."
    )
    parser.add_argument(
        "-o", "--output",
        default="merged.docx",
        help="Output filename (default: merged.docx)."
    )

    args = parser.parse_args()
    merge_documents(args.files, args.output)


if __name__ == "__main__":
    main()
