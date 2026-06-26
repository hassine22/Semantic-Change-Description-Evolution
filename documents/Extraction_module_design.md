# Extraction and Cleaning Module Design

## Objective

The objective of the **Extraction and Cleaning Module** is to collect and prepare semantically meaningful evolutions of change descriptions across patchsets in Gerrit-based projects (**ONAP**, **Wikimedia**, and **LibreOffice**).

Unlike a raw extraction process, this module directly produces a verified dataset by integrating:

* Data extraction
* Text cleaning
* Patchset evolution verification

The final output contains only meaningful changes in change descriptions between consecutive patchsets.

---

# API Investigation

Before implementing the module, several Gerrit REST API endpoints were investigated to determine which endpoint provides the most suitable information for extracting patchset descriptions.

---

## Endpoint 1

### `/changes/{change-id}/detail`

This endpoint provides general metadata about a change, including:

* Change metadata
* Status
* Reviewers
* Messages
* Revision numbers

Although it is useful for identifying all patchsets associated with a change, it does **not** provide the commit message in a structured form suitable for semantic analysis.

Therefore, this endpoint is used **only to discover the revision structure** of a change.

---

## Endpoint 2 (Selected Endpoint)

### `/changes/{change-id}/revisions/{revision-id}/commit`

This endpoint returns detailed information about a specific patchset, including:

* Commit hash
* Subject
* Commit message

Example response:

```json
{
  "subject": "...",
  "message": "..."
}
```

This endpoint was selected because it directly provides the textual change description required for this study.

---

# Endpoint Validation

The selected endpoint was experimentally validated on three independent Gerrit ecosystems:

* LibreOffice
* Wikimedia
* ONAP

For all projects, commit messages were successfully retrieved for multiple patchsets, confirming that the endpoint consistently provides patchset-level change descriptions.

---

# Module Responsibilities

The module performs three tightly coupled operations:

1. Patchset extraction
2. Description cleaning
3. Semantic evolution verification

---

## Step 1 — Extract Patchset Evolution

For each change, the module retrieves every available patchset:

```text
PS1
PS2
PS3
PS4
...
```

It then constructs consecutive patchset pairs:

```text
PS1 → PS2
PS2 → PS3
PS3 → PS4
...
```

For each change, also retrieve the total number of patchsets associated with the change:

```text
patchset_count = total number of revisions (PS1 ... PSN)
```

For every pair, the following information is extracted:

* `change_id`
* `patchset_count`
* `old_patchset`
* `new_patchset`
* `old_description` (raw commit message)
* `new_description` (raw commit message)

---

## Step 2 — Clean Descriptions

Each commit message is cleaned using a predefined set of rules to remove metadata and syntactic noise while preserving its semantic content.

### Removed Elements

The following metadata fields are removed:

* Change-Id
* Signed-off-by
* Reviewed-by
* Tested-by
* Acked-by
* Depends-On
* Hosts
* Co-authored-by
* Reported-by
* Suggested-by
* Cc
* Fixes
* Related
* Bug / Issue / Closes references
* See-also
* Reviewed-on
* Cherry picked from commit
* CI and build metadata

### Text Normalization

The following normalization steps are also applied:

* Whitespace normalization
* Removal of empty lines
* Cleanup of formatting artifacts

> **Important:** The semantic content of the description is intentionally preserved (e.g., *"fix bug"*, *"add feature"*, *"refactor authentication module"*).

---

## Step 3 — Semantic Verification of Evolution

After cleaning, every patchset pair is evaluated to determine whether the description has undergone a meaningful evolution.

A pair is retained **only if**:

```text
clean_old_description ≠ clean_new_description
```

Pairs whose cleaned descriptions are identical are discarded because they do not represent an actual semantic evolution.

---

# Output

The module generates a single dataset:

```text
verified_pairs.csv
```

## Output Schema

| change_id | old_patchset | new_patchset | old_description_clean | new_description_clean | patchset_count |
| --------- | ------------ | ------------ | --------------------- | --------------------- | -------------- |

The **patchset_count** represents the total number of revisions for a given change and is used as a structural feature describing the evolution complexity of the change.

---

# Role of This Module in the Pipeline

This module represents the first major transformation stage of the overall pipeline.

```text
Raw Gerrit Data
        │
        ▼
Extraction + Cleaning + Verification
        │
        ▼
Verified Semantic Pairs Dataset
        │
        ▼
Edit Distance Analysis
```

By combining extraction, cleaning, and semantic verification into a single process, the module guarantees that all downstream analyses operate exclusively on verified instances of meaningful description evolution.
