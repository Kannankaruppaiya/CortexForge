"""Idempotent recording of what a change actually was (sections 10 and 37).

A ``ChangeSet`` is the durable record of one analysed change: which files moved
from what base to what target, and which symbols changed inside them. Two
properties matter and neither held before:

* **The commits mean what they say.** ``base_commit_sha`` is the state analysed
  *from* and ``target_commit_sha`` the state analysed *to*. The previous code
  wrote the base into one field and the project's last indexed commit into the
  other, which made the pair meaningless for anything downstream.
* **Analysing the same change twice yields one changeset.** The idempotency key
  covers the project, both commits, the working-tree flag and the file set, and
  the database enforces uniqueness -- so a redelivered webhook or a re-run job
  cannot fork the project's change history.
"""

import hashlib
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.code_intelligence.treesitter.semantic_diff import (
    SemanticChange,
    SemanticChangeType,
)
from cortexforge.core.models import ChangeSet, FileChange, SymbolChange

logger = logging.getLogger(__name__)

# Git status letters as reported by `git diff --name-status`.
_GIT_STATUS_TO_CHANGE_TYPE: dict[str, str] = {
    "A": "ADDED",
    "D": "DELETED",
    "M": "MODIFIED",
    "R": "RENAMED",
    "C": "ADDED",
    "T": "MODIFIED",
}


def compute_changeset_key(
    project_id: str,
    base_commit_sha: str | None,
    target_commit_sha: str,
    is_working_tree: bool,
    file_paths: list[str],
) -> str:
    """Identity of a change: the project, the commit span, and the files touched."""
    material = "|".join(
        [
            project_id,
            base_commit_sha or "",
            target_commit_sha,
            "1" if is_working_tree else "0",
            ",".join(sorted(file_paths)),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def classify_file_change(
    file_path: str,
    semantic_changes: list[SemanticChange],
    git_status: str | None = None,
    file_exists: bool = True,
    existed_before: bool = True,
) -> str:
    """Decide a file's change type from git status, or infer it from the diff.

    Git's own status is authoritative when available. Otherwise the type is
    inferred from what the semantic diff found and whether the file exists on
    either side -- which is still far better than the previous behaviour of
    labelling every change ``MODIFIED`` regardless of what happened.
    """
    if git_status:
        letter = git_status[0].upper()
        if letter in _GIT_STATUS_TO_CHANGE_TYPE:
            return _GIT_STATUS_TO_CHANGE_TYPE[letter]

    if not file_exists:
        return "DELETED"
    if not existed_before:
        return "ADDED"

    for change in semantic_changes:
        if change.file_path != file_path:
            continue
        if change.change_type == SemanticChangeType.SYMBOL_MOVED:
            return "MOVED"
    return "MODIFIED"


class ChangeSetRecorder:
    """Persists change sets, file changes and symbol changes, exactly once each."""

    async def record(
        self,
        session: AsyncSession,
        project_id: str,
        file_paths: list[str],
        semantic_changes: list[SemanticChange],
        base_commit_sha: str | None,
        target_commit_sha: str,
        is_working_tree: bool = False,
        branch: str | None = None,
        workspace: str | None = None,
        file_statuses: dict[str, str] | None = None,
        existing_files: set[str] | None = None,
    ) -> tuple[ChangeSet, bool]:
        """Record a change set, returning it and whether it was newly created.

        When a matching change set already exists it is returned untouched: its
        file and symbol rows are already correct, and rewriting them would churn
        ids that decisions and snapshots refer to.
        """
        normalized = sorted({p.replace("\\", "/") for p in file_paths})
        key = compute_changeset_key(
            project_id, base_commit_sha, target_commit_sha, is_working_tree, normalized
        )

        existing = await session.execute(
            select(ChangeSet).where(
                ChangeSet.project_id == project_id, ChangeSet.idempotency_key == key
            )
        )
        found = existing.scalars().first()
        if found is not None:
            logger.debug("Reusing change set %s for identical change", found.id)
            return found, False

        change_set = ChangeSet(
            project_id=project_id,
            base_commit_sha=base_commit_sha,
            target_commit_sha=target_commit_sha,
            is_working_tree=is_working_tree,
            branch=branch,
            workspace=workspace,
            idempotency_key=key,
        )
        session.add(change_set)
        await session.flush()

        present = existing_files if existing_files is not None else set(normalized)
        for path in normalized:
            session.add(
                FileChange(
                    change_set_id=change_set.id,
                    file_path=path,
                    change_type=classify_file_change(
                        path,
                        semantic_changes,
                        git_status=(file_statuses or {}).get(path),
                        file_exists=path in present,
                    ),
                )
            )

        for change in semantic_changes:
            session.add(
                SymbolChange(
                    change_set_id=change_set.id,
                    file_path=change.file_path,
                    symbol_name=change.symbol_name,
                    qualified_name=change.qualified_name,
                    entity_type=change.entity_type,
                    change_type=change.change_type.value,
                    old_signature=change.before_signature,
                    new_signature=change.after_signature,
                )
            )

        await session.flush()
        return change_set, True
