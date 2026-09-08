"""Deterministic claim extraction and canonicalization (sections 4 and 25).

A memory is prose written by a person, an agent or an LLM. A *claim* is one
independently evaluable proposition inside it:

    memory : "Authentication uses Redis-backed token revocation and rotates keys
              every 24 hours."
    claims : "authentication uses redis for token revocation"
             "authentication keys rotate every 24 hours"

Extraction here is deliberately deterministic and LLM-free. An LLM may later be
used to *propose* better phrasings, but the identity of a claim -- the thing
deduplication, verification and temporal reasoning key on -- must be reproducible
from the text alone, or replay (section 36) and idempotency (section 37) are
impossible.

Deduplication is deliberately two-tier, because exact folding cannot honestly
collapse everything:

* **Exact identity** -- ``claim_key`` is a hash of the canonical token signature.
  Reorderings, plurals, and known spelling variants of the *same* proposition
  ("JWT signing uses HS256" / "HS256 is used for signing JWTs") collide exactly,
  and the database enforces one row per key (section 25).
* **Near identity** -- differently-worded propositions such as "JWT signing uses
  HS256" and "Authentication tokens are HMAC-SHA256 signed" fold onto a shared
  ``hmac_sha256`` token but retain distinct signatures. They are surfaced by
  :func:`claim_similarity` for review rather than silently merged, because merging
  two propositions that merely overlap is how a memory layer starts asserting
  things nobody wrote.
"""

import hashlib
import re
from dataclasses import dataclass, field

# Tokens carrying no propositional content.
_STOP_WORDS: frozenset[str] = frozenset(
    ["a", "an", "the", "this", "that", "these", "those", "is", "are", "was", "were", "be", "been", "being", "am", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "over", "under", "about", "as", "it", "its", "and", "or", "but", "so", "then", "than", "there", "here", "we", "you", "they", "he", "she", "i", "our", "your", "their", "must", "should", "shall", "will", "would", "can", "could", "may", "might", "do", "does", "did", "done", "via", "which", "who", "whom", "whose", "what", "when", "where", "why", "how", "all", "any", "some", "each", "every", "no", "not", "only", "also", "just", "very", "more", "most", "much", "many", "if", "else", "while", "during", "between", "within", "without", "after", "before", "now", "new", "old", "project", "code", "system", "application", "service", "module", "file", "line", "thing", "stuff"]
)

# Surface-form folding: distinct spellings of one concept map to one token.
# Kept explicit and small; every entry is a claim about the domain, so it is
# reviewable rather than a learned black box.
_SYNONYMS: dict[str, str] = {
    "hs256": "hmac_sha256",
    "hmac-sha256": "hmac_sha256",
    "hmacsha256": "hmac_sha256",
    "hmac_sha256": "hmac_sha256",
    "hmac": "hmac_sha256",
    "sha-256": "sha256",
    "sha_256": "sha256",
    "rs256": "rsa_sha256",
    "jsonwebtoken": "jwt",
    "json-web-token": "jwt",
    "jwts": "jwt",
    "tokens": "token",
    "signed": "sign",
    "signing": "sign",
    "signs": "sign",
    "signature": "sign",
    "authentication": "auth",
    "authenticate": "auth",
    "authn": "auth",
    "authorisation": "authz",
    "authorization": "authz",
    "databases": "database",
    "db": "database",
    "postgres": "postgresql",
    "caching": "cache",
    "cached": "cache",
    "caches": "cache",
    "revoked": "revoke",
    "revocation": "revoke",
    "revocable": "revoke",
    "revoking": "revoke",
    "sessions": "session",
    "endpoints": "endpoint",
    "routes": "route",
    "routing": "route",
    "migrations": "migration",
    "dependencies": "dependency",
    "configuration": "config",
    "configs": "config",
    "environments": "environment",
    "envs": "environment",
    "env": "environment",
    "uses": "use",
    "used": "use",
    "using": "use",
}

# Multi-word phrases folded before tokenization, so that "HMAC-SHA256" becomes one
# concept rather than the two unrelated tokens {hmac, sha256}.
_PHRASE_FOLDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bhmac[\s\-_]*sha[\s\-_]*256\b", re.IGNORECASE), " hmac_sha256 "),
    (re.compile(r"\bhs256\b", re.IGNORECASE), " hmac_sha256 "),
    (re.compile(r"\brsa[\s\-_]*sha[\s\-_]*256\b", re.IGNORECASE), " rsa_sha256 "),
    (re.compile(r"\brs256\b", re.IGNORECASE), " rsa_sha256 "),
    (re.compile(r"\bsha[\s\-_]*256\b", re.IGNORECASE), " sha256 "),
    (re.compile(r"\bjson[\s\-_]*web[\s\-_]*token\b", re.IGNORECASE), " jwt "),
    (re.compile(r"\baccess[\s\-_]*token\b", re.IGNORECASE), " access_token "),
    (re.compile(r"\brefresh[\s\-_]*token\b", re.IGNORECASE), " refresh_token "),
    (re.compile(r"\bpull[\s\-_]*request\b", re.IGNORECASE), " pull_request "),
    (re.compile(r"\bfeature[\s\-_]*flag\b", re.IGNORECASE), " feature_flag "),
]

# Words that look plural but are not; depluralising them produces nonsense
# ("redis" -> "redi", "https" -> "http").
_NOT_PLURAL: frozenset[str] = frozenset(
    ["redis", "https", "tls", "dns", "cors", "jenkins", "kubernetes", "aws", "sas", "gis", "status", "bus", "class", "access", "process", "success", "address", "analysis", "basis"]
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_BULLET_PREFIX = re.compile(r"^\s*(?:[-*]|\d+[.)])\s*")
_WORD = re.compile(r"[A-Za-z0-9_]+")

# Conjunctions that usually join two independent assertions.
_CLAUSE_SPLIT = re.compile(r"\s+(?:and also|;)\s+", re.IGNORECASE)

# Minimum content tokens for a fragment to count as a proposition. Below this a
# fragment is a title or a label, not an assertion.
_MIN_CONTENT_TOKENS = 3


@dataclass(frozen=True)
class ExtractedClaim:
    """One proposition extracted from a memory, with its canonical identity."""

    text: str
    canonical_text: str
    claim_key: str
    subject: str | None = None
    predicate: str | None = None
    tokens: tuple[str, ...] = field(default_factory=tuple)


def normalize_token(token: str) -> str:
    """Fold one token to its canonical surface form."""
    low = token.lower()
    if low in _SYNONYMS:
        return _SYNONYMS[low]
    # Naive depluralisation, skipped for words that only look plural and for
    # results that would collapse into a stop word.
    if (
        len(low) > 4
        and low.endswith("s")
        and not low.endswith(("ss", "us", "is"))
        and low not in _NOT_PLURAL
    ):
        singular = low[:-1]
        if singular in _SYNONYMS:
            return _SYNONYMS[singular]
        if singular not in _STOP_WORDS:
            return singular
    return low


def fold_phrases(text: str) -> str:
    """Apply multi-word concept folding before tokenization."""
    folded = text or ""
    for pattern, replacement in _PHRASE_FOLDS:
        folded = pattern.sub(replacement, folded)
    return folded


def canonicalize(text: str) -> tuple[str, tuple[str, ...]]:
    """Reduce a proposition to a stable canonical signature.

    Returns ``(canonical_text, content_tokens)``. The canonical text is the sorted,
    deduplicated, synonym-folded content token set joined by spaces -- word order
    and phrasing are intentionally discarded so that logically identical statements
    written differently produce one identity (section 25).
    """
    raw_tokens = _WORD.findall(fold_phrases(text))
    folded = [normalize_token(t) for t in raw_tokens]
    content = sorted({t for t in folded if t and t not in _STOP_WORDS and len(t) > 1})
    return " ".join(content), tuple(content)


def compute_claim_key(
    project_id: str,
    canonical_text: str,
    scope: str = "PROJECT",
    scope_ref: str | None = None,
) -> str:
    """Stable logical identity for a claim within a project and scope.

    Two memories asserting the same proposition about the same scope share a key,
    which is what makes deduplication a database invariant rather than a heuristic.
    """
    material = " ".join(
        [project_id or "", (scope or "PROJECT").upper(), scope_ref or "", canonical_text or ""]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _split_propositions(text: str) -> list[str]:
    """Split prose into candidate propositions."""
    fragments: list[str] = []
    for raw_line in (text or "").splitlines():
        line = _BULLET_PREFIX.sub("", raw_line).strip()
        if not line:
            continue
        for sentence in _SENTENCE_SPLIT.split(line):
            stripped = sentence.strip()
            if not stripped:
                continue
            for clause in _CLAUSE_SPLIT.split(stripped):
                cleaned = clause.strip(" .;:-")
                if cleaned:
                    fragments.append(cleaned)
    return fragments


_COPULA = frozenset({"is", "are", "was", "were", "use", "has", "have", "must", "should"})


def _derive_subject_predicate(text: str) -> tuple[str | None, str | None]:
    """Best-effort subject/predicate split, used for display and grouping only."""
    words = text.split()
    if len(words) < 3:
        return None, None
    for idx, word in enumerate(words[:6]):
        if normalize_token(word) in _COPULA:
            subject = " ".join(words[:idx]).strip()
            predicate = " ".join(words[idx:]).strip()
            if subject:
                return subject[:255], predicate
    return " ".join(words[:3])[:255], " ".join(words[3:])


def extract_claims(
    project_id: str,
    text: str,
    title: str | None = None,
    scope: str = "PROJECT",
    scope_ref: str | None = None,
    max_claims: int = 12,
) -> list[ExtractedClaim]:
    """Extract deduplicated, canonically keyed claims from memory text.

    The memory title is considered only when the body yields no proposition, so
    that a titled memory is never claim-less while a substantive body is preferred.
    Claims are deduplicated by ``claim_key`` within the result, so a memory that
    repeats itself produces one claim (section 37).
    """
    fragments = _split_propositions(text)
    if not fragments and title:
        fragments = _split_propositions(title)

    seen: set[str] = set()
    claims: list[ExtractedClaim] = []

    for fragment in fragments:
        canonical, tokens = canonicalize(fragment)
        if len(tokens) < _MIN_CONTENT_TOKENS:
            continue
        key = compute_claim_key(project_id, canonical, scope, scope_ref)
        if key in seen:
            continue
        seen.add(key)
        subject, predicate = _derive_subject_predicate(fragment)
        claims.append(
            ExtractedClaim(
                text=fragment[:2000],
                canonical_text=canonical,
                claim_key=key,
                subject=subject,
                predicate=predicate,
                tokens=tokens,
            )
        )
        if len(claims) >= max_claims:
            break

    # A memory whose body is too terse to yield a proposition still gets one claim
    # from title+body, so that every memory is verifiable rather than opaque.
    if not claims:
        seed = f"{title or ''} {text or ''}".strip()
        canonical, tokens = canonicalize(seed)
        if tokens:
            key = compute_claim_key(project_id, canonical, scope, scope_ref)
            subject, predicate = _derive_subject_predicate(seed)
            claims.append(
                ExtractedClaim(
                    text=seed[:2000],
                    canonical_text=canonical,
                    claim_key=key,
                    subject=subject,
                    predicate=predicate,
                    tokens=tokens,
                )
            )

    return claims


def claim_similarity(
    a_tokens: tuple[str, ...] | list[str], b_tokens: tuple[str, ...] | list[str]
) -> float:
    """Jaccard overlap of canonical token sets.

    Used to find *near*-duplicate claims that did not collide exactly. It is a
    similarity, never a truth signal: two claims can be textually near-identical
    and still disagree about the repository, which is what verification is for.
    """
    set_a, set_b = set(a_tokens), set(b_tokens)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)
