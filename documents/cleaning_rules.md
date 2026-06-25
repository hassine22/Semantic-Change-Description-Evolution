# Cleaning Rules

## Objective

The purpose of the cleaning phase is to remove metadata, boilerplate text, and syntactic artifacts that do not contribute to the semantic content of change descriptions while preserving meaningful information.

---

# Level 1: Metadata Removal

The following Gerrit metadata lines will be removed entirely:

```text
Change-Id:
Signed-off-by:
Reviewed-by:
Tested-by:
Acked-by:
Depends-On:
Hosts:
Co-authored-by:
Reported-by:
Suggested-by:
Cc:
Reviewed-on:
```

Examples:

```text
Reviewed-by: John Doe
Signed-off-by: Alice Smith
Change-Id: I123456789
```

These lines do not contribute to the description's semantic content.

---

# Level 2: Boilerplate Removal

The following automatically generated patterns will be removed:

```text
cherry picked from commit
Cherry-picked-from
(cherry picked from commit ...)
```

```text
WIP
Work In Progress
DO NOT MERGE
```

These statements describe the review status rather than the change itself.

---

# Level 3: Issue Reference Normalization

Issue references will be removed when they appear as standalone tracking information.

Examples:

```text
Bug: 12345
Issue: 67890
Closes: #123
Related: #456
Fixes: JIRA-123
```

However, issue references embedded within meaningful sentences will be preserved.

Example:

```text
Fixes a memory leak reported in Bug 12345
```

This sentence will be retained because it contributes semantic information.

---

# Level 4: Text Normalization

The following transformations will be applied:

## Whitespace normalization

Convert:

```text
multiple     spaces
```

to:

```text
multiple spaces
```

## Empty line normalization

Remove consecutive empty lines.

## Leading/trailing spaces

Remove spaces at the beginning and end of lines.

---

# Preservation Rules

The following information must NOT be removed:

## Technical rationale

Example:

```text
Fix race condition caused by concurrent updates.
```

## Design decisions

Example:

```text
Use caching to reduce database requests.
```

## Testing information

Example:

```text
Validated using integration tests.
```

## Build information

Example:

```text
Updated Maven configuration to support Java 21.
```

## Limitations and constraints

Example:

```text
This solution only applies to IPv6 deployments.
```

---

# Verification Rule

After cleaning:

```text
old_description_clean
new_description_clean
```

A pair will be retained only if:

```text
old_description_clean != new_description_clean
```

Otherwise, the pair will be discarded because no meaningful description evolution remains after cleaning.
