"""
================================================================================
extract_and_clean.py
================================================================================
Research Pipeline: Semantic Evolution of Change Descriptions in Gerrit Projects
--------------------------------------------------------------------------------
Implements Module 1 of the pipeline defined in:
  - piplinedesign.md            → overall pipeline architecture
  - cleaning_rules.md           → all cleaning levels (L1–L4) and preservation rules
  - Extraction_module_design.md → API endpoint decisions and module responsibilities

Pipeline stage covered:
  Raw Gerrit Data
        │
        ▼
  Extraction + Cleaning + Verification   ← THIS SCRIPT
        │
        ▼
  verified_pairs.csv
        │
        ▼
  Edit Distance Analysis  (next module)

Author  : Research Pipeline – Senior Python / Data Engineering
Python  : 3.9+
Deps    : requests  (pip install requests)
================================================================================
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

import requests


# ================================================================================
# SECTION 1 — RUN-MODE CONFIGURATION
# ================================================================================
#
# RUN_MODE controls how many changes are fetched per project:
#
#   "test"   → TEST_LIMIT changes per enabled project  (safe for quick validation)
#   "full"   → No limit; runs every change in the project  (production / thesis)
#   "custom" → Each project uses its own sample_limit defined in PROJECTS below
#
RUN_MODE: str = "test"      # ← change to "full" or "custom" when ready

# Number of changes to process per project when RUN_MODE = "test"
TEST_LIMIT: int = 100

# ================================================================================
# SECTION 2 — PROJECT CONFIGURATION
# ================================================================================
#
# To skip a project  → set "enabled": False
# To cap a project   → set "sample_limit": N  (used only when RUN_MODE = "custom")
#
PROJECTS: dict[str, dict] = {
    "wikimedia": {
        "enabled": True,
        "base_url": "https://gerrit.wikimedia.org/r",
        # api_prefix — extra path segment inserted between base_url and every
        # REST endpoint.  Empty string = standard Gerrit layout (no extra prefix).
        # ONAP and Wikimedia expose the REST API directly under /r/, so no
        # prefix is needed: all paths resolve as  /r/changes/…
        "api_prefix": "",
        "sample_limit": None,
    },
    "libreoffice": {
        "enabled": True,
        #
        # ── LibreOffice Gerrit URL topology (confirmed from error logs) ──────
        #
        # LibreOffice Gerrit serves the PolyGerrit SPA (Single Page App) at
        # ALL paths under /r/, including /r/a/.  The SPA is a JavaScript shell
        # that returns:
        #       <!DOCTYPE html><html lang="en"><meta charset="utf-8">
        #       <meta name="description" content="Gerrit Code Review">…
        # regardless of what sub-path is requested under /r/.
        #
        # The REST API is mounted at the DOMAIN ROOT, not under /r/:
        #
        #   ✘  https://gerrit.libreoffice.org/r/changes/    → SPA HTML
        #   ✘  https://gerrit.libreoffice.org/r/a/changes/  → SPA HTML
        #   ✔  https://gerrit.libreoffice.org/changes/      → JSON  ← CORRECT
        #
        # Deployment layout (LibreOffice / TDF infrastructure):
        #   /r/        →  nginx serves PolyGerrit SPA (web UI, HTML)
        #   /          →  nginx reverse-proxies to Gerrit REST dispatcher (JSON)
        #
        # This is the OPPOSITE of ONAP/Wikimedia, where /r/ IS the REST root.
        #
        # Fix: strip the /r suffix from base_url entirely.
        # api_prefix = "" because anonymous REST at the domain root needs no
        # extra path segment — /changes/ resolves correctly on its own.
        #
        "base_url": "https://gerrit.libreoffice.org",
        "api_prefix": "",
        "sample_limit": None,
    },
    "onap": {
        "enabled": True,
        "base_url": "https://gerrit.onap.org/r",
        # Same as Wikimedia — standard layout, no prefix needed.
        "api_prefix": "",
        "sample_limit": None,
    },
}

# ================================================================================
# SECTION 3 — OUTPUT CONFIGURATION
# ================================================================================

OUTPUT_DIR: Path = Path("output")
OUTPUT_FILE: str = "verified_pairs.csv"

# Column order — matches Extraction_module_design.md output schema exactly
CSV_COLUMNS: list[str] = [
    "project",
    "change_id",
    "patchset_count",
    "old_patchset",
    "new_patchset",
    "old_message_clean",
    "new_message_clean",
]

# ================================================================================
# SECTION 4 — CLEANING CONFIGURATION
# ================================================================================
#
# Set any key to False to DISABLE that cleaning rule — no code changes needed.
#
CLEANING_CONFIG: dict[str, bool] = {
    # Level 1 — Gerrit metadata lines (Change-Id, Signed-off-by, …)
    "remove_metadata": True,
    # Level 2 — Boilerplate lines (cherry picked, WIP, DO NOT MERGE, …)
    "remove_keywords": True,
    # Level 3 — Standalone issue references (Bug: 123, Closes: #45, …)
    #            Embedded references ("Fixes a memory leak in Bug 123") are KEPT
    "remove_bug_references": True,
    # Level 4 — Whitespace / empty-line / leading-trailing-space normalisation
    "normalize_whitespace": True,
}

# ================================================================================
# SECTION 5 — NETWORK / RETRY CONFIGURATION
# ================================================================================

REQUEST_TIMEOUT: int   = 30     # seconds per HTTP request
MAX_RETRIES: int       = 5      # maximum retry attempts per request
RETRY_BACKOFF_BASE: float = 2.0 # exponential-backoff base (seconds)
RETRY_BACKOFF_MAX: float  = 60.0 # cap on backoff wait time (seconds)

# Gerrit paginates via ?S=<offset>; this controls how many changes per page
PAGE_SIZE: int = 500

# ================================================================================
# SECTION 6 — REAL-TIME PROGRESS CONFIGURATION
# ================================================================================
#
# PROGRESS_EVERY = 1  → print a line after EVERY single change (maximum detail)
# PROGRESS_EVERY = 10 → print every 10 changes
# PROGRESS_EVERY = 500 → print every 500 changes (good for millions of changes)
#
PROGRESS_EVERY: int = 1   # ← set to 1 to see every change in real time

# ================================================================================
# SECTION 7 — LOGGING SETUP
# ================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("extract_and_clean.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ================================================================================
# SECTION 8 — CLEANING FUNCTIONS  (cleaning_rules.md — Levels 1–4)
# ================================================================================

# ── Level 1 ─ Metadata Removal ──────────────────────────────────────────────
# All prefixes listed in cleaning_rules.md §Level 1 + pipeline_design.md §Step2

_METADATA_PREFIXES: tuple[str, ...] = (
    "change-id:",
    "signed-off-by:",
    "reviewed-by:",
    "tested-by:",
    "acked-by:",
    "depends-on:",
    "hosts:",
    "co-authored-by:",
    "reported-by:",
    "suggested-by:",
    "cc:",
    "reviewed-on:",
    "see-also:",
)


def remove_metadata(text: str) -> str:
    """
    Level 1 — Remove Gerrit metadata lines.
    A line is dropped when it starts with any known metadata prefix
    (case-insensitive, tolerating optional leading whitespace).
    """
    out: list[str] = []
    for line in text.splitlines():
        if not any(line.strip().lower().startswith(p) for p in _METADATA_PREFIXES):
            out.append(line)
    return "\n".join(out)


# ── Level 2 ─ Boilerplate Removal ───────────────────────────────────────────

_KEYWORD_LINE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*\(cherry picked from commit\s+[0-9a-f]+\)\s*$", re.IGNORECASE),
    re.compile(r"^\s*cherry[- ]picked[- ]from\s*:?\s*\S*\s*$",       re.IGNORECASE),
    re.compile(r"^\s*cherry picked from commit\s+\S+\s*$",            re.IGNORECASE),
    re.compile(r"^\s*(WIP|Work In Progress|DO NOT MERGE)\s*$",        re.IGNORECASE),
]

_INLINE_CHERRY_PICK: re.Pattern = re.compile(
    r"\(cherry picked from commit\s+[0-9a-f]+\)", re.IGNORECASE
)


def remove_keywords(text: str) -> str:
    """
    Level 2 — Remove boilerplate whole-lines and inline cherry-pick annotations.
    """
    out: list[str] = []
    for line in text.splitlines():
        if any(pat.match(line) for pat in _KEYWORD_LINE_PATTERNS):
            continue
        line = _INLINE_CHERRY_PICK.sub("", line).rstrip()
        out.append(line)
    return "\n".join(out)


# ── Level 3 ─ Issue Reference Normalisation ──────────────────────────────────
# Standalone tracking lines are dropped; embedded references are preserved
# (cleaning_rules.md §Level 3 Preservation Rule).

_STANDALONE_ISSUE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*bug\s*[:#]?\s*\S+\s*$",            re.IGNORECASE),
    re.compile(r"^\s*issue\s*[:#]?\s*\S+\s*$",          re.IGNORECASE),
    re.compile(r"^\s*closes\s*[:#]?\s*\S+\s*$",         re.IGNORECASE),
    re.compile(r"^\s*related(?:-to)?\s*[:#]?\s*\S+\s*$",re.IGNORECASE),
    re.compile(r"^\s*fixes\s*[:#]\s*\S+\s*$",           re.IGNORECASE),
    re.compile(r"^\s*see-also\s*[:#]?\s*\S+\s*$",       re.IGNORECASE),
]


def remove_bug_references(text: str) -> str:
    """
    Level 3 — Remove standalone issue-reference lines.
    Lines consisting solely of an issue token are discarded.
    Lines where the reference is part of a sentence are kept.
    """
    out: list[str] = []
    for line in text.splitlines():
        if not any(pat.match(line) for pat in _STANDALONE_ISSUE_PATTERNS):
            out.append(line)
    return "\n".join(out)


# ── Level 4 ─ Text Normalisation ─────────────────────────────────────────────

def normalize_whitespace(text: str) -> str:
    """
    Level 4 — Strip per-line edges, collapse internal spaces,
    collapse consecutive blank lines to one, strip overall edges.
    """
    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"  +", " ", line.strip())
        lines.append(line)

    collapsed: list[str] = []
    prev_blank = False
    for line in lines:
        is_blank = (line == "")
        if is_blank and prev_blank:
            continue
        collapsed.append(line)
        prev_blank = is_blank

    return "\n".join(collapsed).strip()


# ── Master Cleaning Orchestrator ─────────────────────────────────────────────

_CLEANING_PIPELINE: list[tuple[str, object]] = [
    ("remove_metadata",       remove_metadata),
    ("remove_keywords",       remove_keywords),
    ("remove_bug_references", remove_bug_references),
    ("normalize_whitespace",  normalize_whitespace),
]


def clean_description(raw: str, config: dict[str, bool] = CLEANING_CONFIG) -> str:
    """
    Apply all configured cleaning levels in fixed order L1 → L2 → L3 → L4.
    Disable any level by setting its key to False in CLEANING_CONFIG.
    """
    text = raw or ""
    for key, fn in _CLEANING_PIPELINE:
        if config.get(key, True):
            text = fn(text)  # type: ignore[operator]
    return text


# ================================================================================
# SECTION 9 — GERRIT API CLIENT
# ================================================================================
#
# Endpoint 1 (revision discovery):
#   /changes/?q=...&o=ALL_REVISIONS
#
# Endpoint 2 (commit message per patchset):
#   /changes/{change-id}/revisions/{revision-hash}/commit
#
# CRITICAL: Gerrit prepends  )]}'\n  to every JSON response to prevent
# JSON hijacking (XSSI protection).  This prefix MUST be stripped before
# calling json.loads().  Additionally some Gerrit deployments return:
#   • HTTP 302 → redirect to an auth / login page with an empty body
#   • HTTP 200 + empty body  (no matching changes)
#   • HTTP 200 + plain-text error  (proxy / WAF rejection)
#   • HTTP 200 + HTML error page  (some reverse-proxy configs)
# All of these must be handled gracefully without crashing the pipeline.


def _parse_gerrit_response(resp: requests.Response) -> list | dict:
    """
    Strip Gerrit's XSSI prefix and parse the JSON body.

    Raises:
        ValueError  — body is empty, not JSON, or is an HTML/plain-text error page.
        RuntimeError — HTTP status indicates a server-side error.
    """
    body: str = resp.text

    # ── Strip every known Gerrit XSSI prefix variant ──────────────────────
    # Standard:   )]}'\n
    # Compact:    )]}'
    # Some older: )]}
    for prefix in (")]}'\n", ")]}'", ")]}"):
        if body.startswith(prefix):
            body = body[len(prefix):]
            break

    body = body.strip()

    if not body:
        raise ValueError(
            f"Empty response body from {resp.url} "
            f"(HTTP {resp.status_code}). "
            "Likely causes: no matching changes, server redirect to auth page, "
            "or proxy returning empty 200."
        )

    # Guard against HTML error pages (reverse-proxy / WAF rejections)
    if body.lstrip().startswith("<"):
        # Extract a short hint from the HTML <title> if present
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, re.IGNORECASE | re.S)
        hint = title_match.group(1).strip() if title_match else body[:120]
        raise ValueError(
            f"Server returned HTML instead of JSON from {resp.url}: {hint!r}"
        )

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        snippet = body[:200].replace("\n", " ")
        raise ValueError(
            f"JSON decode error from {resp.url}: {exc}. "
            f"Body snippet: {snippet!r}"
        ) from exc


@dataclass
class GerritClient:
    """
    Gerrit REST API client with automatic retry / exponential backoff.

    Attributes:
        base_url   : Root URL of the Gerrit instance (no trailing slash).
                     e.g. "https://gerrit.wikimedia.org/r"
        api_prefix : Extra path segment inserted between base_url and every
                     REST endpoint path.
                       ""   → standard layout  (ONAP, Wikimedia)
                       "/a" → authenticated REST prefix  (LibreOffice)
                     With api_prefix="/a" a path like "/changes/?q=…" becomes
                     "/a/changes/?q=…", resolving to the Gerrit /a/ sub-tree
                     that is guaranteed to return JSON and never HTML.
        session    : Persistent requests.Session (connection pooling + headers).
    """

    base_url:   str
    api_prefix: str = ""          # set to "/a" for LibreOffice
    session: requests.Session = field(default_factory=requests.Session, repr=False)

    def __post_init__(self) -> None:
        """
        Configure the session so every request carries the headers that
        force Gerrit (and any reverse-proxy in front of it) to serve JSON.

        Accept: application/json
            Tells content-negotiating proxies (nginx, Cloudflare Workers,
            Varnish) to route the request to the REST handler rather than the
            HTML web-UI handler.  This is the second line of defence after
            the /a/ prefix for LibreOffice; it is also harmless for ONAP and
            Wikimedia which ignore it.

        X-Gerrit-Auth: 1
            Some Gerrit deployments check for this header to distinguish REST
            clients from browsers.  Adding it never hurts on standard instances.
        """
        self.session.headers.update({
            "Accept":        "application/json",
            "X-Gerrit-Auth": "1",
        })

    # ── Internal HTTP layer ──────────────────────────────────────────────────

    def _get(self, path: str) -> dict | list:
        """
        GET request with exponential-backoff retry.

        Constructs the full URL as:
            base_url + api_prefix + path
        Examples:
            ONAP/Wikimedia  → "https://gerrit.onap.org/r"      + ""   + "/changes/…"
                            = "https://gerrit.onap.org/r/changes/…"
            LibreOffice     → "https://gerrit.libreoffice.org/r" + "/a" + "/changes/…"
                            = "https://gerrit.libreoffice.org/r/a/changes/…"

        The /a/ sub-path is Gerrit's documented authenticated REST namespace.
        It is handled exclusively by the REST dispatcher and never intercepted
        by the web-UI router, so it always returns JSON.

        Raises:
            FileNotFoundError — genuine 404 (resource does not exist).
            RuntimeError      — all retries exhausted.
        """
        url = f"{self.base_url}{self.api_prefix}{path}"
        attempt = 0
        last_exc: Exception | None = None

        while attempt < MAX_RETRIES:
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                return _parse_gerrit_response(resp)

            # ── Transient network errors ─────────────────────────────────
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                wait = min(RETRY_BACKOFF_BASE ** attempt, RETRY_BACKOFF_MAX)
                log.warning("  ↻ Timeout  %s  (attempt %d/%d) — retry in %.0fs",
                            url, attempt + 1, MAX_RETRIES, wait)
                time.sleep(wait)

            # ── HTTP errors ──────────────────────────────────────────────
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 404:
                    raise FileNotFoundError(f"404 Not Found: {url}") from exc
                if status in (429, 500, 502, 503, 504):
                    last_exc = exc
                    wait = min(RETRY_BACKOFF_BASE ** attempt, RETRY_BACKOFF_MAX)
                    log.warning("  ↻ HTTP %d  %s  (attempt %d/%d) — retry in %.0fs",
                                status, url, attempt + 1, MAX_RETRIES, wait)
                    time.sleep(wait)
                else:
                    # 401, 403, etc. — non-retryable
                    raise RuntimeError(
                        f"Non-retryable HTTP {status} from {url}"
                    ) from exc

            # ── Malformed / empty body (the LibreOffice scenario) ────────
            except ValueError as exc:
                # Not retryable — body is structurally wrong.
                # Raise immediately so the caller can skip gracefully.
                raise RuntimeError(str(exc)) from exc

            # ── Other connection errors ──────────────────────────────────
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                wait = min(RETRY_BACKOFF_BASE ** attempt, RETRY_BACKOFF_MAX)
                log.warning("  ↻ Network error  %s  (attempt %d/%d): %s — retry in %.0fs",
                            url, attempt + 1, MAX_RETRIES, exc, wait)
                time.sleep(wait)

            attempt += 1

        raise RuntimeError(
            f"All {MAX_RETRIES} retries exhausted for {url}"
        ) from last_exc

    # ── Public API ───────────────────────────────────────────────────────────

    def list_changes(self, limit: Optional[int] = None) -> Iterator[dict]:
        """
        Paginate through changes in this Gerrit instance.

        Args:
            limit: Stop after yielding this many changes.  None = no limit.

        Yields:
            Raw change dicts from the Gerrit API.
        """
        offset  = 0
        yielded = 0

        while True:
            page_size = PAGE_SIZE
            if limit is not None:
                remaining = limit - yielded
                if remaining <= 0:
                    break
                page_size = min(PAGE_SIZE, remaining)

            path = (
                f"/changes/?q=status:merged+OR+status:open+OR+status:abandoned"
                f"&n={page_size}&S={offset}"
                f"&o=ALL_REVISIONS&o=ALL_COMMITS"
            )

            try:
                changes = self._get(path)
            except RuntimeError as exc:
                log.error("  ✘ Failed to fetch change list at offset %d: %s", offset, exc)
                break

            # Some Gerrit versions return an empty list (not an error) when
            # there are no more results — guard against that here.
            if not changes:
                break

            for change in changes:
                yield change
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

            if not changes[-1].get("_more_changes", False):
                break

            offset += len(changes)

    def get_commit_message(self, change_id: str, revision_id: str) -> Optional[str]:
        """
        Fetch the raw commit message for a specific patchset.

        Endpoint 2 from Extraction_module_design.md:
            /changes/{change-id}/revisions/{revision-id}/commit

        Args:
            change_id:   Gerrit change identifier.
            revision_id: Full SHA-1 hash of the revision (NOT the patchset number).

        Returns:
            Raw commit message string, or None if unavailable.
        """
        path = f"/changes/{change_id}/revisions/{revision_id}/commit"
        try:
            data = self._get(path)
            return data.get("message", "")  # type: ignore[union-attr]
        except FileNotFoundError:
            log.debug("  Commit not found: change=%s  revision=%s", change_id, revision_id)
            return None
        except RuntimeError as exc:
            log.debug("  Could not fetch commit %s/%s: %s", change_id, revision_id, exc)
            return None


# ================================================================================
# SECTION 10 — PATCHSET PAIR EXTRACTION
# ================================================================================


@dataclass
class PatchsetPair:
    """Holds one consecutive patchset pair with its cleaned descriptions."""
    project:          str
    change_id:        str
    patchset_count:   int
    old_patchset:     int
    new_patchset:     int
    old_message_clean: str
    new_message_clean: str


def extract_pairs_from_change(
    change: dict,
    client: GerritClient,
    project_name: str,
) -> list[PatchsetPair]:
    """
    Extract all consecutive patchset pairs for a single Gerrit change.

    Algorithm (Extraction_module_design.md §Step 1):
      1. Read all revisions (already embedded via ALL_REVISIONS).
      2. Sort by _number (integer) — not by hash or insertion order.
      3. Build pairs: (PS_i, PS_{i+1}).
      4. Fetch commit messages for each revision.
      5. Clean both messages.
      6. Discard pair if clean_old == clean_new  (verification rule).

    Returns:
        List of verified PatchsetPair instances (may be empty).
    """
    revisions: dict = change.get("revisions", {})
    if not revisions:
        return []

    change_id: str = change.get("id", change.get("change_id", ""))
    if not change_id:
        return []

    # Sort revision hashes by patchset number (_number is an integer)
    sorted_revs: list[tuple[int, str]] = sorted(
        ((rev_data["_number"], rev_hash) for rev_hash, rev_data in revisions.items()),
        key=lambda x: x[0],
    )

    patchset_count = len(sorted_revs)
    if patchset_count < 2:
        return []   # single-patchset change — no evolution possible

    # Cache so each revision hash is fetched at most once per change
    msg_cache: dict[str, Optional[str]] = {}

    def get_msg(rev_hash: str) -> Optional[str]:
        if rev_hash not in msg_cache:
            msg_cache[rev_hash] = client.get_commit_message(change_id, rev_hash)
        return msg_cache[rev_hash]

    pairs: list[PatchsetPair] = []

    for i in range(patchset_count - 1):
        old_num, old_hash = sorted_revs[i]
        new_num, new_hash = sorted_revs[i + 1]

        old_raw = get_msg(old_hash)
        new_raw = get_msg(new_hash)

        if old_raw is None or new_raw is None:
            continue     # commit message unavailable — skip silently

        old_clean = clean_description(old_raw)
        new_clean = clean_description(new_raw)

        # Verification rule (cleaning_rules.md §Verification Rule)
        if old_clean == new_clean:
            continue     # only metadata changed — no semantic evolution

        pairs.append(PatchsetPair(
            project=project_name,
            change_id=change_id,
            patchset_count=patchset_count,
            old_patchset=old_num,
            new_patchset=new_num,
            old_message_clean=old_clean,
            new_message_clean=new_clean,
        ))

    return pairs


# ================================================================================
# SECTION 11 — PROJECT PROCESSING ENGINE
# ================================================================================


def _resolve_limit(project_name: str, project_cfg: dict) -> Optional[int]:
    """Return the effective change-limit based on RUN_MODE."""
    if RUN_MODE == "test":
        return TEST_LIMIT
    if RUN_MODE == "full":
        return None
    if RUN_MODE == "custom":
        return project_cfg.get("sample_limit")
    log.warning("Unknown RUN_MODE '%s' — falling back to TEST_LIMIT.", RUN_MODE)
    return TEST_LIMIT


def _fmt(n: int) -> str:
    """Format an integer with thousands separator."""
    return f"{n:,}"


def process_project(
    project_name: str,
    project_cfg: dict,
    writer: "csv.DictWriter",
) -> dict[str, int]:
    """
    Run the full extraction + cleaning + verification pipeline for one project.

    Real-time progress is printed after every PROGRESS_EVERY changes so you
    always see what the pipeline is doing right now.

    Returns a stats dict with total_changes, processed_changes,
    extracted_pairs, skipped_single_ps, skipped_identical, verified_pairs.
    """
    base_url: str       = project_cfg["base_url"]
    api_prefix: str     = project_cfg.get("api_prefix", "")
    limit: Optional[int] = _resolve_limit(project_name, project_cfg)
    label: str          = project_name.upper()

    # ── Project header ───────────────────────────────────────────────────────
    log.info("")
    log.info("┌─────────────────────────────────────────────────────────────────┐")
    log.info("│  Processing project : %-43s│", label)
    log.info("│  Base URL           : %-43s│", base_url)
    log.info("│  API prefix         : %-43s│", api_prefix if api_prefix else "(none)")
    log.info("│  Run mode           : %-8s  Limit : %-28s│",
             RUN_MODE, _fmt(limit) if limit is not None else "unlimited")
    log.info("└─────────────────────────────────────────────────────────────────┘")

    client = GerritClient(base_url=base_url, api_prefix=api_prefix)

    stats: dict[str, int] = {
        "total_changes":    0,
        "processed_changes":0,
        "extracted_pairs":  0,
        "skipped_single_ps":0,   # changes with only 1 patchset
        "skipped_identical":0,   # pairs dropped because clean_old == clean_new
        "verified_pairs":   0,
    }

    for change in client.list_changes(limit=limit):
        stats["total_changes"] += 1
        n = stats["total_changes"]
        change_id_short = change.get("id", change.get("change_id", "?"))[:40]

        # ── Attempt pair extraction ─────────────────────────────────────
        try:
            revisions = change.get("revisions", {})
            ps_count  = len(revisions)

            if ps_count < 2:
                stats["skipped_single_ps"] += 1
                if n % PROGRESS_EVERY == 0:
                    log.info(
                        "  [%s] #%-6s  change=%-40s  ps=%d  "
                        "→ SKIP (single patchset)  │ total=%s  verified=%s",
                        label, _fmt(n), change_id_short, ps_count,
                        _fmt(stats["total_changes"]), _fmt(stats["verified_pairs"]),
                    )
                continue

            pairs = extract_pairs_from_change(change, client, project_name)

        except Exception as exc:
            log.warning("  [%s] #%s  ERROR processing change %s: %s",
                        label, _fmt(n), change_id_short, exc)
            continue

        stats["processed_changes"] += 1

        # Count how many pairs were dropped at the identical-after-clean step
        potential = max(ps_count - 1, 0)
        verified  = len(pairs)
        skipped_clean = potential - verified
        stats["extracted_pairs"]   += potential
        stats["skipped_identical"] += skipped_clean
        stats["verified_pairs"]    += verified

        # Write verified pairs to CSV immediately (streaming — no memory build-up)
        for pair in pairs:
            writer.writerow({
                "project":           pair.project,
                "change_id":         pair.change_id,
                "patchset_count":    pair.patchset_count,
                "old_patchset":      pair.old_patchset,
                "new_patchset":      pair.new_patchset,
                "old_message_clean": pair.old_message_clean,
                "new_message_clean": pair.new_message_clean,
            })

        # ── Real-time progress line ─────────────────────────────────────
        if n % PROGRESS_EVERY == 0:
            log.info(
                "  [%s] #%-6s  change=%-40s  ps=%-3d  "
                "pairs=%-3d  identical_dropped=%-3d  "
                "│ cumul → extracted=%-6s  verified=%-6s",
                label,
                _fmt(n),
                change_id_short,
                ps_count,
                potential,
                skipped_clean,
                _fmt(stats["extracted_pairs"]),
                _fmt(stats["verified_pairs"]),
            )

    # ── Per-project final summary ─────────────────────────────────────────────
    log.info("")
    log.info("  ╔══ %s — Results ══════════════════════════════════════════╗", label)
    log.info("  ║  Total changes fetched         : %-30s║", _fmt(stats["total_changes"]))
    log.info("  ║  Skipped (single patchset)     : %-30s║", _fmt(stats["skipped_single_ps"]))
    log.info("  ║  Changes processed             : %-30s║", _fmt(stats["processed_changes"]))
    log.info("  ║  Pairs extracted (potential)   : %-30s║", _fmt(stats["extracted_pairs"]))
    log.info("  ║  Dropped (identical after clean): %-29s║", _fmt(stats["skipped_identical"]))
    log.info("  ║  Verified pairs written to CSV : %-30s║", _fmt(stats["verified_pairs"]))
    log.info("  ╚══════════════════════════════════════════════════════════════╝")
    log.info("")

    return stats


# ================================================================================
# SECTION 12 — MAIN ENTRY POINT
# ================================================================================


def main() -> None:
    """
    Orchestrate the extraction + cleaning pipeline across all enabled projects.
    """
    # ── Validate run mode ────────────────────────────────────────────────────
    valid_modes = {"test", "full", "custom"}
    if RUN_MODE not in valid_modes:
        log.error("Invalid RUN_MODE '%s'. Choose from: %s", RUN_MODE, valid_modes)
        sys.exit(1)

    enabled = {n: c for n, c in PROJECTS.items() if c.get("enabled", False)}
    if not enabled:
        log.error("No projects enabled. Set enabled=True for at least one project.")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / OUTPUT_FILE

    # ── Banner ───────────────────────────────────────────────────────────────
    log.info("╔══════════════════════════════════════════════════════════════════╗")
    log.info("║   Gerrit Semantic Change Description Evolution — Module 1        ║")
    log.info("╚══════════════════════════════════════════════════════════════════╝")
    log.info("  Run mode         : %s", RUN_MODE)
    log.info("  Test limit       : %s changes/project", _fmt(TEST_LIMIT) if RUN_MODE == "test" else "n/a")
    log.info("  Progress every   : every %d change(s)", PROGRESS_EVERY)
    log.info("  Enabled projects : %s", ", ".join(enabled.keys()))
    log.info("  Output file      : %s", output_path)
    log.info("  Active cleaning  : %s",
             ", ".join(k for k, v in CLEANING_CONFIG.items() if v))
    log.info("")

    global_stats: dict[str, dict] = {}

    # Open CSV once — all projects append to the same file (streaming writes)
    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for project_name, project_cfg in enabled.items():
            try:
                stats = process_project(project_name, project_cfg, writer)
                global_stats[project_name] = stats
            except KeyboardInterrupt:
                log.warning("Interrupted — partial results saved to %s", output_path)
                raise
            except Exception as exc:
                log.error("Fatal error in project %s: %s", project_name, exc)
                global_stats[project_name] = {"error": str(exc)}

    # ── Global summary ───────────────────────────────────────────────────────
    log.info("╔══════════════════════════════════════════════════════════════════╗")
    log.info("║                    GLOBAL PIPELINE SUMMARY                       ║")
    log.info("╚══════════════════════════════════════════════════════════════════╝")

    total_verified = 0
    for pname, stats in global_stats.items():
        if "error" in stats:
            log.info("  %-15s  ✘  ERROR: %s", pname.upper(), stats["error"])
        else:
            vp = stats.get("verified_pairs", 0)
            total_verified += vp
            log.info(
                "  %-15s  ✔  changes=%-8s  extracted=%-8s  "
                "identical_dropped=%-8s  verified=%-8s",
                pname.upper(),
                _fmt(stats.get("total_changes", 0)),
                _fmt(stats.get("extracted_pairs", 0)),
                _fmt(stats.get("skipped_identical", 0)),
                _fmt(vp),
            )

    log.info("")
    log.info("  Total verified pairs written : %s", _fmt(total_verified))
    log.info("  Output file                  : %s", output_path.resolve())
    log.info("")
    log.info("  Next pipeline stage → Edit Distance Module (pairs_with_distance.csv)")


# ================================================================================
# SECTION 13 — SCRIPT ENTRY
# ================================================================================

if __name__ == "__main__":
    main()