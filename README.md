### Batch prompting OpenAI deep research API

This little project implements easy prompting of OpenAI deep research API using batches. The results are exported into Word documents.


### How to use

Steps:

1. Generate raw prompts: use `prepare_prompts.ipynb` notebook and populate your folder with **raw prompts**:

2. Refine prompts:
    ```bash
    python refine_prompts.py <raw prompts folder> <refined prompts folder>
    ```
    If needed, adjust parameters like `--model` (GPT-4.1 by default). `top_p` is hardcoded as 0.1 but you can adjust it as well.

3. Submit deep research prompt batch:
    ```bash
    python submit_batch.py <refined prompts folder> <SQLite database file name>
    ```

    If needed, adjust parameters like `--max-tool-calls`, `--model`, `--instructions`.

4. Check and download deep research batch results:
    ```bash
    python poll_batches.py <SQLite database file name>
    ```

5. Export results as MS Word documents:
    ```bash
    python export_docx_from_db.py <SQLite database file name> --output-dir <folder with docx files>
    ```

6. (optional) Merge individual docx files into one big docx:
    ```bash
    python merge_docx.py <docx files selection> -o <merged.docx>
    ```

**Important**

This code was implemented using ChatGPT-5.1 and 5.2 (with some necessary manual fixes). It works but use at your own risk.

#### License

MIT
