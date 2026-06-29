"""
run_prompts.py
==============
Prompt Engineering Evaluation Tool for Semantic Commit Description Analysis.

Queries a locally running Ollama instance (http://localhost:11434).
Make sure `ollama serve` is running before executing this script.

Usage
-----
    python run_prompts.py

All configuration lives in the LLM_CONFIG block below.

Architecture
------------
    load_prompt()           — reads and normalises a prompt template from disk
    load_gold_data()        — parses the semicolon-delimited gold CSV
    fill_prompt()           — substitutes {old_description} / {new_description}
    query_llm()             — sends the prompt to Ollama and returns raw text
    parse_response()        — extracts and validates the JSON payload
    run_prompt_evaluation() — orchestrates one full prompt x dataset pass
    write_results()         — persists results to an output CSV
    main()                  — entry point; loops over all prompt files

Adding a new prompt
-------------------
Drop a new file (e.g. prompts/prompt6.txt) into the prompt directory.
The script discovers all prompt*.txt files automatically — no code change needed.
"""

import csv
import glob
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# =============================================================================
# LLM CONFIGURATION (CENTRALIZED)
# This is the only block you need to edit to change model or paths.
# =============================================================================

LLM_CONFIG: dict[str, Any] = {
    # Ollama must be running: `ollama serve`
    # Verify with: curl http://localhost:11434/api/generate -d "{\"model\":\"mistral\",\"prompt\":\"hi\",\"stream\":false}"
    "ollama_url": "http://localhost:11434/api/generate",

    # Model name exactly as shown in `ollama list`
    "model": "mistral",

    # Generation parameters
    "temperature": 0.2,   # Low = more deterministic (recommended for classification)
    "max_tokens":  512,   # Max tokens to generate per response

    # Reproducibility
    "seed": 42,

    # Paths  — use forward slashes or raw strings on Windows
    "prompt_dir": "prompts/",      # folder containing prompt1.txt … prompt5.txt
    "output_dir": "output/",      # folder where result CSVs are written
    "data_file":  "data/gold_data.csv", # gold-standard dataset
}

# =============================================================================
# CONSTANTS
# =============================================================================

# Columns always present in the input dataset
BASE_INPUT_COLUMNS = ["change_id", "old_description", "new_description", "human_label"]

# LLM response field names that get renamed in the output
STANDARD_DECISION_KEY = "decision"
STANDARD_REASON_KEY   = "reason"

# Retry settings for transient Ollama errors
MAX_RETRIES = 3
RETRY_DELAY = 2   # seconds between retries

# =============================================================================
# PROMPT LOADING
# =============================================================================

def load_prompt(prompt_path: str) -> str:
    """
    Load a prompt template from disk and normalise it.

    Handles two common artefacts:
    - Outer surrounding quotes added by some editors (stripped)
    - CSV-style double-double-quotes ("" → ") from spreadsheet exports

    Raises FileNotFoundError if the file is missing.
    Raises ValueError if {old_description} or {new_description} are absent.
    """
    if not os.path.isfile(prompt_path):
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    with open(prompt_path, "r", encoding="utf-8") as fh:
        raw = fh.read()

    template = raw.strip()

    # Remove optional outer quotation marks (e.g. the file starts/ends with ")
    if template.startswith('"') and template.endswith('"'):
        template = template[1:-1]

    # Collapse CSV-escaped double quotes: "" → "
    template = template.replace('""', '"')

    # Validate placeholders
    missing = [p for p in ("{old_description}", "{new_description}") if p not in template]
    if missing:
        raise ValueError(
            f"Prompt '{prompt_path}' is missing placeholders: {missing}"
        )

    return template


def discover_prompts(prompt_dir: str) -> list[tuple[str, str]]:
    """
    Find all prompt*.txt files in prompt_dir, sorted alphabetically.

    Returns a list of (stem, full_path) tuples,
    e.g. [("prompt1", "prompts/prompt1.txt"), …]
    """
    if not os.path.isdir(prompt_dir):
        raise FileNotFoundError(f"Prompt directory not found: {prompt_dir!r}")

    found = sorted(glob.glob(os.path.join(prompt_dir, "prompt*.txt")))

    if not found:
        raise RuntimeError(
            f"No 'prompt*.txt' files found in '{prompt_dir}'. "
            "Please place your prompt templates there."
        )

    return [(Path(p).stem, p) for p in found]


# =============================================================================
# DATA LOADING
# =============================================================================

def load_gold_data(data_file: str) -> list[dict[str, str]]:
    """
    Parse the semicolon-delimited gold CSV.

    Expected columns: change_id, old_description, new_description, human_label.
    All field values are stripped of surrounding whitespace.
    """
    if not os.path.isfile(data_file):
        raise FileNotFoundError(f"Gold data file not found: {data_file}")

    rows: list[dict[str, str]] = []

    with open(data_file, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";", quotechar='"')

        if reader.fieldnames is None:
            raise ValueError("Gold data CSV appears to be empty.")

        missing_cols = [c for c in BASE_INPUT_COLUMNS if c not in reader.fieldnames]
        if missing_cols:
            raise ValueError(
                f"Gold data CSV is missing columns: {missing_cols}. "
                f"Found: {list(reader.fieldnames)}"
            )

        for row in reader:
            rows.append({k: (v.strip() if v else "") for k, v in row.items()})

    return rows


# =============================================================================
# PROMPT FILLING
# =============================================================================

def fill_prompt(template: str, row: dict[str, str]) -> str:
    """
    Replace {old_description} and {new_description} with values from row.
    Returns the fully rendered prompt string ready to send to Ollama.
    """
    return (
        template
        .replace("{old_description}", row["old_description"])
        .replace("{new_description}", row["new_description"])
    )


# =============================================================================
# OLLAMA QUERY
# =============================================================================

def query_llm(prompt: str, config: dict[str, Any]) -> str:
    """
    Send *prompt* to the local Ollama instance and return the response text.

    Retries up to MAX_RETRIES times on transient errors (network hiccup,
    Ollama temporarily busy, etc.).

    Raises RuntimeError if all attempts fail.
    """
    import requests

    payload = {
        "model":  config["model"],
        "prompt": prompt,
        "stream": False,          # wait for the complete response
        "options": {
            "temperature": config.get("temperature", 0.2),
            "num_predict": config.get("max_tokens", 512),
            "seed":        config.get("seed", 42),
        },
    }

    url = config.get("ollama_url", "http://localhost:11434/api/generate")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, timeout=120)

            if response.status_code != 200:
                raise RuntimeError(
                    f"Ollama returned HTTP {response.status_code}: {response.text[:200]}"
                )

            data = response.json()
            return data.get("response", "")

        except Exception as exc:
            if attempt < MAX_RETRIES:
                print(
                    f"    [WARN] Attempt {attempt}/{MAX_RETRIES} failed: {exc}. "
                    f"Retrying in {RETRY_DELAY}s …"
                )
                time.sleep(RETRY_DELAY)
            else:
                raise RuntimeError(
                    f"Ollama query failed after {MAX_RETRIES} attempts: {exc}"
                ) from exc


# =============================================================================
# RESPONSE PARSING
# =============================================================================

def parse_response(raw_text: str) -> dict[str, str]:
    """
    Extract a JSON object from the model's raw output.

    Strategy:
    1. Try parsing the entire string as JSON.
    2. If that fails, search for the first { … } block and parse that.
       This handles models that write prose before or after the JSON.
    3. Rename 'decision' → 'llm_decision', 'reason' → 'llm_reason'.
    4. Pass through any extra keys (category, differences, …) unchanged.
    5. On complete failure, return llm_decision = "PARSE_ERROR".
    """
    parsed: dict | None = None

    # Attempt 1: full string
    try:
        parsed = json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract first {...} block
    if parsed is None:
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
            except json.JSONDecodeError:
                pass

    # Complete failure
    if parsed is None or not isinstance(parsed, dict):
        return {
            "llm_decision": "PARSE_ERROR",
            "llm_reason":   raw_text[:200].replace("\n", " "),
        }

    # Normalise field names
    result: dict[str, str] = {}
    for key, value in parsed.items():
        str_val = str(value).strip() if value is not None else ""
        if key == STANDARD_DECISION_KEY:
            result["llm_decision"] = str_val.upper()
        elif key == STANDARD_REASON_KEY:
            result["llm_reason"] = str_val
        else:
            result[key] = str_val   # category, differences, etc.

    result.setdefault("llm_decision", "MISSING")
    result.setdefault("llm_reason",   "")

    # Tolerate minor variations like "KEEP." or "keep"
    decision = result["llm_decision"]
    if decision not in ("KEEP", "REMOVE", "PARSE_ERROR", "MISSING"):
        if "KEEP" in decision:
            result["llm_decision"] = "KEEP"
        elif "REMOVE" in decision:
            result["llm_decision"] = "REMOVE"

    return result


# =============================================================================
# OUTPUT CSV
# =============================================================================

def _build_output_columns(extra_fields: list[str]) -> list[str]:
    """
    Build the ordered column list for the output CSV.

    Order: base input columns → extra LLM fields → llm_decision → llm_reason
    """
    columns = list(BASE_INPUT_COLUMNS)
    for field in extra_fields:
        if field not in columns:
            columns.append(field)
    columns += ["llm_decision", "llm_reason"]
    return columns


def write_results(
    results:      list[dict[str, str]],
    output_path:  str,
    extra_fields: list[str],
) -> None:
    """Write results for one prompt to a semicolon-delimited CSV file."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    columns = _build_output_columns(extra_fields)

    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=columns,
            delimiter=";",
            extrasaction="ignore",
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        for row in results:
            writer.writerow(row)


# =============================================================================
# EVALUATION LOOP (ONE PROMPT)
# =============================================================================

def run_prompt_evaluation(
    prompt_name: str,
    prompt_path: str,
    gold_rows:   list[dict[str, str]],
    config:      dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate one prompt template against the full gold dataset.

    Prints real-time progress to stdout and returns a summary dict.
    """
    display_name = prompt_name.replace("prompt", "Prompt ").title()

    print(f"\n{'=' * 60}")
    print(f"  Running {display_name}")
    print(f"  Template : {prompt_path}")
    print(f"  Model    : {config['model']}  (Ollama)")
    print(f"{'=' * 60}")

    template     = load_prompt(prompt_path)
    results:      list[dict[str, str]] = []
    extra_fields: list[str]            = []
    n_success    = 0
    n_errors     = 0
    total        = len(gold_rows)

    for idx, row in enumerate(gold_rows, start=1):

        # 1. Fill the template
        filled = fill_prompt(template, row)

        # 2. Query Ollama
        try:
            raw_text  = query_llm(filled, config)
            llm_fields = parse_response(raw_text)
        except Exception as exc:
            print(f"    [ERROR] Row {idx} — {exc}")
            llm_fields = {
                "llm_decision": "LLM_ERROR",
                "llm_reason":   str(exc)[:200],
            }

        # 3. Track success / errors
        decision = llm_fields.get("llm_decision", "")
        if decision in ("KEEP", "REMOVE"):
            n_success += 1
        else:
            n_errors += 1
            if decision not in ("LLM_ERROR",):   # LLM_ERROR already printed above
                print(
                    f"    [WARN] Row {idx} — unexpected decision: '{decision}'"
                )

        # 4. Collect any new extra fields (category, differences, …)
        for key in llm_fields:
            if key not in ("llm_decision", "llm_reason") and key not in extra_fields:
                extra_fields.append(key)

        # 5. Merge and store
        results.append({**row, **llm_fields})

        # 6. Progress line
        print(f"  Processed: {idx:>3} / {total}  →  {decision}")

    # Write output CSV
    output_filename = f"{prompt_name}_results.csv"
    output_path     = os.path.join(config["output_dir"], output_filename)
    write_results(results, output_path, extra_fields)

    # Per-prompt summary
    print(f"\n  ── {display_name} completed ──")
    print(f"  Examples processed : {total}")
    print(f"  Successful         : {n_success}")
    print(f"  Errors             : {n_errors}")
    print(f"  Output             : {output_path}")

    return {
        "prompt_name":  prompt_name,
        "display_name": display_name,
        "results":      results,
        "extra_fields": extra_fields,
        "n_success":    n_success,
        "n_errors":     n_errors,
        "output_path":  output_path,
        "total":        total,
    }


# =============================================================================
# ACCURACY METRIC
# =============================================================================

def compute_accuracy(results: list[dict[str, str]]) -> float:
    """Fraction of rows where llm_decision matches human_label."""
    if not results:
        return 0.0
    correct = sum(
        1 for r in results
        if r.get("llm_decision", "") == r.get("human_label", "").strip()
    )
    return correct / len(results)


# =============================================================================
# GLOBAL SUMMARY
# =============================================================================

def print_global_summary(summaries: list[dict[str, Any]]) -> None:
    """Print a formatted comparison table for all evaluated prompts."""
    print(f"\n{'=' * 60}")
    print("  GLOBAL SUMMARY")
    print(f"  Completed at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")
    print(f"  {'Prompt':<12}  {'Total':>5}  {'OK':>5}  {'Errors':>6}  {'Accuracy':>9}  Output")
    print(f"  {'-'*12}  {'-'*5}  {'-'*5}  {'-'*6}  {'-'*9}  {'-'*30}")

    for s in summaries:
        acc = compute_accuracy(s["results"])
        print(
            f"  {s['display_name']:<12}  "
            f"{s['total']:>5}  "
            f"{s['n_success']:>5}  "
            f"{s['n_errors']:>6}  "
            f"{acc:>8.1%}  "
            f"{s['output_path']}"
        )

    print(f"{'=' * 60}\n")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main() -> int:
    print("\n" + "=" * 60)
    print("  Prompt Engineering Evaluation Tool")
    print(f"  Started at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Model      : {LLM_CONFIG['model']}  (Ollama)")
    print(f"  Endpoint   : {LLM_CONFIG['ollama_url']}")
    print("=" * 60)

    # Load gold data
    try:
        gold_rows = load_gold_data(LLM_CONFIG["data_file"])
    except (FileNotFoundError, ValueError) as exc:
        print(f"\n[FATAL] {exc}", file=sys.stderr)
        return 1
    print(f"\n  Gold dataset loaded : {len(gold_rows)} examples")

    # Discover prompts
    try:
        prompts = discover_prompts(LLM_CONFIG["prompt_dir"])
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"\n[FATAL] {exc}", file=sys.stderr)
        return 1
    print(f"  Prompts discovered  : {len(prompts)}")
    for name, path in prompts:
        print(f"    • {name}  ({path})")

    # Run each prompt
    summaries: list[dict[str, Any]] = []
    for prompt_name, prompt_path in prompts:
        try:
            summary = run_prompt_evaluation(
                prompt_name=prompt_name,
                prompt_path=prompt_path,
                gold_rows=gold_rows,
                config=LLM_CONFIG,
            )
            summaries.append(summary)
        except Exception as exc:
            print(f"\n[ERROR] {prompt_name} aborted: {exc}", file=sys.stderr)
            summaries.append({
                "prompt_name":  prompt_name,
                "display_name": prompt_name.replace("prompt", "Prompt ").title(),
                "results":      [],
                "extra_fields": [],
                "n_success":    0,
                "n_errors":     len(gold_rows),
                "output_path":  "N/A (aborted)",
                "total":        len(gold_rows),
            })

    print_global_summary(summaries)
    return 0


if __name__ == "__main__":
    sys.exit(main())