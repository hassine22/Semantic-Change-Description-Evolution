# Prompt Engineering Evaluation Tool

A reusable research tool for evaluating multiple prompting strategies against a
manually labelled gold-standard dataset of commit description pairs.

---

## Overview

This tool automates the following workflow:

```
gold_data.csv  ──►  [prompt template]  ──►  LLM  ──►  outputs/promptN_results.csv
```

For each prompt template it:

1. Loads the template from `prompts/promptN.txt`
2. Iterates over every row in `gold_data.csv`
3. Substitutes `{old_description}` and `{new_description}` with the row's values
4. Sends the completed prompt to the configured LLM
5. Parses the JSON response
6. Writes the results to `outputs/promptN_results.csv`

---

## Repository Layout

```
project/
├── run_prompts.py          ← main script (this tool)
├── gold_data.csv           ← gold-standard dataset (60 labelled examples)
├── prompts/
│   ├── prompt1.txt
│   ├── prompt2.txt
│   ├── prompt3.txt
│   ├── prompt4.txt
│   └── prompt5.txt
└── outputs/                ← created automatically on first run
    ├── prompt1_results.csv
    ├── prompt2_results.csv
    ├── prompt3_results.csv
    ├── prompt4_results.csv
    └── prompt5_results.csv
```

---

## Dependencies

### Python version

Python 3.10 or later is required (uses `dict[str, Any]` type annotations).

### Provider: Anthropic API (default)

```bash
pip install requests
```

Set your API key as an environment variable:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Provider: Local Mistral 7B

```bash
pip install transformers torch accelerate
```

No API key is required.  The model is downloaded from HuggingFace on first run
(approximately 14 GB for the 7B instruct model).  A CUDA-capable GPU is
strongly recommended.

---

## Configuration

All model settings are centralised in the `LLM_CONFIG` dictionary at the top
of `run_prompts.py`.  **This is the only block you need to edit.**

```python
LLM_CONFIG = {
    # Provider: "anthropic" (API) or "local" (HuggingFace)
    "provider": "anthropic",

    # Model identifier
    # Anthropic: "claude-sonnet-4-6", "claude-opus-4-6", …
    # Local:     "mistralai/Mistral-7B-Instruct-v0.2" or a local folder path
    "model": "claude-sonnet-4-6",

    # Generation parameters
    "temperature": 0.2,   # 0.0 = fully deterministic
    "max_tokens":  512,

    # Local-only settings (ignored when provider == "anthropic")
    "batch_size": 8,
    "device":    "auto",  # "cuda" | "cpu" | "auto"

    # Reproducibility
    "seed": 42,

    # File paths
    "prompt_dir": "prompts/",
    "output_dir": "outputs/",
    "data_file":  "gold_data.csv",
}
```

### Switching to Mistral 7B local

```python
LLM_CONFIG = {
    "provider":    "local",
    "model":       "mistralai/Mistral-7B-Instruct-v0.2",
    "temperature": 0.2,
    "max_tokens":  256,
    "batch_size":  8,
    "device":      "auto",   # set "cuda" to force GPU
    "seed":        42,
    "prompt_dir":  "prompts/",
    "output_dir":  "outputs/",
    "data_file":   "gold_data.csv",
}
```

---

## File Placement

| File | Where to place it |
|------|-------------------|
| `gold_data.csv` | Project root (or update `data_file` in `LLM_CONFIG`) |
| `prompt1.txt` … `prompt5.txt` | `prompts/` directory (or update `prompt_dir`) |
| `run_prompts.py` | Project root |

### Prompt file format

Each prompt file must:

- Be a plain UTF-8 text file
- Contain the placeholders `{old_description}` and `{new_description}`
- Return a JSON object with at minimum `"decision"` and `"reason"` fields

Example (`prompts/prompt3.txt`):

```text
Ignore changes involving only:
- punctuation
- whitespace
…

Old description:
{old_description}

New description:
{new_description}

Return ONLY valid JSON:
{
  "decision": "KEEP" or "REMOVE",
  "reason": "Short explanation."
}
```

### Gold data format

`gold_data.csv` must be semicolon-delimited with the following columns:

```
change_id ; old_description ; new_description ; human_label
```

`human_label` must be either `KEEP` or `REMOVE`.

---

## Execution

```bash
python run_prompts.py
```

The script will print real-time progress for each prompt:

```
============================================================
  Running Prompt 1
  Template: prompts/prompt1.txt
============================================================
  Processed:   1 / 60  →  KEEP
  Processed:   2 / 60  →  REMOVE
  …
  Processed:  60 / 60  →  KEEP

  ── Prompt 1 completed ──
  Examples processed : 60
  Successful         : 58
  Errors             : 2
  Output             : outputs/prompt1_results.csv
```

After all prompts finish, a global summary is printed:

```
============================================================
  GLOBAL SUMMARY
  Completed at: 2026-06-29 14:32:01
============================================================
  Prompt        Total      OK  Errors  Accuracy  Output
  ----------  ------  ------  ------  ---------  -----
  Prompt 1        60      58       2      78.3%  outputs/prompt1_results.csv
  Prompt 2        60      59       1      81.7%  outputs/prompt2_results.csv
  …
============================================================
```

---

## Output Files

One CSV is generated per prompt inside `outputs/`.

### Standard columns (all prompts)

| Column | Description |
|--------|-------------|
| `change_id` | Unique identifier for the Gerrit change |
| `old_description` | Earlier version of the commit description |
| `new_description` | Later version of the commit description |
| `human_label` | Gold-standard label (`KEEP` / `REMOVE`) |
| `llm_decision` | Model's classification (`KEEP` / `REMOVE`) |
| `llm_reason` | Model's explanation |

### Extra columns (prompt-specific)

| Prompt | Extra columns |
|--------|---------------|
| Prompt 4 | `differences` — meaningful differences identified between the two descriptions |
| Prompt 5 | `category` — evolution category (e.g. `Technical Detail Added`, `Formatting`) |

The script detects extra fields automatically at runtime, so any new field
returned by a future prompt is included in its output CSV without code changes.

---

## Extending the Tool

### Adding a new prompt

1. Create `prompts/prompt6.txt` with the standard placeholders.
2. Run `python run_prompts.py` — the script discovers prompt files automatically.

No code changes are required.

### Adding a new provider

Add a new `_query_<provider>()` function following the same signature as
`_query_local()` and `_query_anthropic()`, then register it in `query_llm()`:

```python
elif provider == "openai":
    return _query_openai(prompt, config)
```

---

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| LLM API error | Retried up to 3 times with a 2-second delay; recorded as `LLM_ERROR` if all attempts fail |
| Unparseable JSON response | Recorded as `PARSE_ERROR`; first 200 chars of raw output stored in `llm_reason` |
| Missing prompt file | Fatal error with a clear message; script exits with code 1 |
| Missing gold data | Fatal error with a clear message; script exits with code 1 |
| One prompt fails entirely | Logged as an error; remaining prompts continue |

---

## Reproducibility

Set `"seed": 42` and `"temperature": 0.2` in `LLM_CONFIG` to maximise
consistency across runs.  For fully deterministic output with a local model,
set `"temperature": 0.0`.

Note: The Anthropic API may produce slightly different outputs between calls
even at low temperature due to infrastructure non-determinism.
