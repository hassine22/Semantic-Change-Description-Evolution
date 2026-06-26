# Extraction and Cleaning Module Design

## Objective

The objective of the Extraction and Cleaning Module is to collect and prepare semantically meaningful evolution of change descriptions across patchsets in Gerrit-based projects (ONAP, Wikimedia, LibreOffice).

Unlike a raw extraction process, this module directly produces a verified dataset by integrating:
- data extraction
- text cleaning
- patchset evolution verification

The final output represents only meaningful changes in descriptions across consecutive patchsets.

---

## API Investigation

Before implementation, several Gerrit REST API endpoints were investigated.

### Endpoint 1

```text
/changes/{change-id}/detail

This endpoint provides general metadata about a change, including:

change metadata
status
reviewers
messages
revision numbers

It is used to identify all patchsets associated with a change but does not provide the commit message content in a structured way suitable for analysis.

Therefore, it is used only for discovering revision structure.

## Endpoint 2 (Selected Endpoint)
/changes/{change-id}/revisions/{revision-id}/commit

This endpoint returns detailed information about a specific patchset, including:

commit hash
subject
commit message

Example:

{
  "subject": "...",
  "message": "..."
}

This endpoint was selected because it directly provides the textual change description required for this study.

Endpoint Validation

The selected endpoint was experimentally validated on three independent Gerrit ecosystems:

LibreOffice
Wikimedia
ONAP

For all projects, commit messages were successfully retrieved for multiple patchsets, confirming that the endpoint consistently provides patchset-level change descriptions.

## Module Responsibilities

This module performs three tightly coupled operations:

## Step 1 — Extract Patchset Evolution

For each change:

Retrieve all patchsets:

PS1, PS2, PS3, PS4, ...

Construct consecutive pairs:

PS1 → PS2
PS2 → PS3
PS3 → PS4

For each pair, extract:

change_id
old_patchset
new_patchset
old_description (raw commit message)
new_description (raw commit message)
##Step 2 — Clean Descriptions

Each commit message is cleaned using predefined rules in order to remove syntactic and metadata noise.

Removed elements:
Change-Id
Signed-off-by
Reviewed-by
Tested-by
Acked-by
Depends-On
Hosts
Co-authored-by
Reported-by
Suggested-by
Cc
Fixes
Related
Bug / Issue / Closes references
See-also
Reviewed-on
Cherry picked from commit
CI and build metadata
Normalization:
whitespace normalization
removal of empty lines
formatting artifacts cleanup

⚠️ Semantic content is preserved (e.g., “fix bug”, “add feature”, “refactor”).

Step 3 — Semantic Verification of Evolution

After cleaning, patchset pairs are filtered to retain only meaningful evolution.

A pair is kept only if:

clean_old_description ≠ clean_new_description

This ensures that only true semantic changes across patchsets are preserved.

Output

The module produces:

verified_pairs.csv
Output Schema
change_id	old_patchset	new_patchset	old_description_clean	new_description_clean
Role of This Module in the Pipeline

This module acts as the first major transformation step in the pipeline:

Raw Gerrit Data
        ↓
Extraction + Cleaning + Verification
        ↓
Verified Semantic Pairs Dataset
        ↓
Edit Distance Analysis

It ensures that all subsequent modules operate only on meaningful semantic evolution instances.