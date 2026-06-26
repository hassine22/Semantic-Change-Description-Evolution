# Extraction Module Design

## Objective

The objective of the Extraction Module is to collect the evolution of change descriptions across patchsets from Gerrit projects.

For each change, the module retrieves the description associated with every patchset and stores it for further processing.

---

## API Investigation

Before implementing the extraction module, several Gerrit REST API endpoints were investigated.

### Endpoint 1

```text
/changes/{change-id}/detail
```

This endpoint provides general information about a change, including:

* change metadata
* status
* reviewers
* messages
* revision numbers

The endpoint allowed us to identify all patchsets associated with a change. However, it did not directly provide the commit message (change description) of each patchset.

Therefore, this endpoint is useful for discovering revision numbers but is not sufficient for extracting change descriptions.

---

### Endpoint 2

```text
/changes/{change-id}/revisions/{revision-id}/commit
```

This endpoint returns detailed information about a specific revision (patchset), including:

* commit hash
* subject
* commit message

Example:

```json
{
  "subject": "...",
  "message": "..."
}
```

Experiments on LibreOffice changes confirmed that this endpoint returns the complete commit message associated with a given patchset.

Since the commit message corresponds to the change description studied in this work, this endpoint was selected as the primary data source.

---

## Justification of the Selected Endpoint

The endpoint

```text
/changes/{change-id}/revisions/{revision-id}/commit
```

was selected because:

1. It provides the description of a specific patchset.
2. It allows access to all patchsets of a change.
3. It enables reconstruction of description evolution by comparing descriptions from consecutive patchsets.
4. It returns the exact data required for the study without relying on heuristics.

Therefore, the evolution of change descriptions can be operationalized as the evolution of commit messages across patchsets.

---
## Endpoint Selection and Validation

Several Gerrit REST API endpoints were investigated to identify a reliable source of change descriptions across patchsets.

The endpoint

/changes/{change-id}/revisions/{revision-id}/commit

was selected because it directly returns the commit information associated with a specific patchset, including:

- subject
- commit message

The endpoint was experimentally validated on the three Gerrit ecosystems considered in this study:

- LibreOffice
- Wikimedia
- ONAP

For each project, commit messages were successfully retrieved for individual patchsets, demonstrating that the endpoint consistently provides access to patchset-level change descriptions.

Consequently, change-description evolution is operationalized as the evolution of commit messages across successive patchsets.

## Extraction Procedure

For each change:

1. Retrieve all available patchset numbers.
2. For each patchset:

   * query the endpoint

```text
/changes/{change-id}/revisions/{revision-id}/commit
```

3. Extract:

   * Change ID
   * Patchset Number
   * Subject
   * Commit Message
4. Store the extracted information.

---

## Output Format

The extraction module produces a CSV file containing one row per patchset.

| change_id | patchset | subject | message |
| --------- | -------- | ------- | ------- |
| 120477    | 1        | ...     | ...     |
| 120477    | 2        | ...     | ...     |
| 120477    | 3        | ...     | ...     |

This dataset serves as the input for the cleaning and verification modules.
