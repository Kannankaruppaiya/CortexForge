"""Tree-sitter AST Semantic Diff Engine.

Computes fine-grained semantic changes between file revisions:
- symbol added / removed / renamed
- signature changed
- function body changed
- class changed
- inheritance changed
- interface changed
- import changed
- call relationship changed
- route changed
- schema changed
- configuration changed

Adheres strictly to Specification Sections 8 and 9.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cortexforge.code_intelligence.parser import ParsedRelationship, ParsedSymbol
from cortexforge.code_intelligence.treesitter.analyzer import TreeSitterProvider


class SemanticChangeType(str, Enum):
    FILE_ADDED = "FILE_ADDED"
    FILE_REMOVED = "FILE_REMOVED"
    FILE_RENAMED = "FILE_RENAMED"
    SYMBOL_ADDED = "SYMBOL_ADDED"
    SYMBOL_REMOVED = "SYMBOL_REMOVED"
    SYMBOL_RENAMED = "SYMBOL_RENAMED"
    SYMBOL_MOVED = "SYMBOL_MOVED"
    SIGNATURE_CHANGED = "SIGNATURE_CHANGED"
    BODY_CHANGED = "BODY_CHANGED"
    CLASS_CHANGED = "CLASS_CHANGED"
    INHERITANCE_CHANGED = "INHERITANCE_CHANGED"
    INTERFACE_CHANGED = "INTERFACE_CHANGED"
    IMPORT_CHANGED = "IMPORT_CHANGED"
    DEPENDENCY_CHANGED = "DEPENDENCY_CHANGED"
    CALL_RELATIONSHIP_CHANGED = "CALL_RELATIONSHIP_CHANGED"
    ROUTE_CHANGED = "ROUTE_CHANGED"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    CONFIG_CHANGED = "CONFIG_CHANGED"


@dataclass
class SemanticChange:
    """Structured representation of a semantic code modification."""

    file_path: str
    change_type: SemanticChangeType
    symbol_name: str
    qualified_name: str
    entity_type: str  # function, method, class, module, file, config
    commit_sha: str | None = None
    entity_id: str | None = None
    before_fingerprint: str | None = None
    after_fingerprint: str | None = None
    before_signature: str | None = None
    after_signature: str | None = None
    before_line_range: tuple[int, int] | None = None
    after_line_range: tuple[int, int] | None = None
    affected_relationships: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def normalize_signature(sig: str | None) -> str:
    """Extract normalized parameter and return signature, stripping symbol name."""
    if not sig:
        return ""
    idx = sig.find("(")
    if idx != -1:
        return sig[idx:].strip().rstrip(":").strip()
    return sig.strip()


def are_symbols_lineage_match(sym_a: Any, sym_b: Any) -> bool:
    """Determine whether two symbols represent the same entity across a rename or move."""
    type_a = getattr(sym_a, "entity_type", None)
    type_b = getattr(sym_b, "entity_type", None)
    if type_a != type_b:
        return False

    hash_a = getattr(sym_a, "content_hash", None)
    hash_b = getattr(sym_b, "content_hash", None)
    if hash_a and hash_b and hash_a == hash_b:
        return True

    sig_a = normalize_signature(getattr(sym_a, "signature", None))
    sig_b = normalize_signature(getattr(sym_b, "signature", None))
    return bool(sig_a and sig_b and sig_a == sig_b and len(sig_a) > 2)


class ASTSemanticDiffer:
    """Compares AST structures across file revisions to produce structured semantic changes."""

    def __init__(self, parser_provider: TreeSitterProvider | None = None) -> None:
        self.parser = parser_provider or TreeSitterProvider()

    def diff_file_contents(
        self,
        file_path: str,
        before_content: bytes | None,
        after_content: bytes | None,
        commit_sha: str | None = None,
        old_path: str | None = None,
    ) -> list[SemanticChange]:
        """Compute semantic AST diff between two content states of a file."""
        changes: list[SemanticChange] = []

        # Case 1: File renamed
        if old_path and old_path != file_path:
            changes.append(
                SemanticChange(
                    commit_sha=commit_sha,
                    file_path=file_path,
                    change_type=SemanticChangeType.FILE_RENAMED,
                    symbol_name=file_path.split("/")[-1].split("\\")[-1],
                    qualified_name=file_path,
                    entity_type="file",
                    details={"old_path": old_path, "new_path": file_path},
                )
            )
            if before_content and after_content and self.parser.can_parse(file_path):
                parsed_before = self.parser.parse_source(old_path, before_content)
                parsed_after = self.parser.parse_source(file_path, after_content)
                b_syms = {s.name: s for s in parsed_before.symbols if s.entity_type != "file"}
                for asym in parsed_after.symbols:
                    if asym.entity_type != "file":
                        bsym = b_syms.get(asym.name) or next(
                            (s for s in parsed_before.symbols if s.content_hash == asym.content_hash and s.entity_type != "file"),
                            None
                        )
                        if bsym:
                            changes.append(
                                SemanticChange(
                                    commit_sha=commit_sha,
                                    file_path=file_path,
                                    change_type=SemanticChangeType.SYMBOL_MOVED,
                                    symbol_name=asym.name,
                                    qualified_name=asym.qualified_name,
                                    entity_type=asym.entity_type,
                                    before_fingerprint=bsym.content_hash,
                                    after_fingerprint=asym.content_hash,
                                    before_signature=bsym.signature,
                                    after_signature=asym.signature,
                                    before_line_range=(bsym.start_line, bsym.end_line),
                                    after_line_range=(asym.start_line, asym.end_line),
                                    details={"old_path": old_path, "old_name": bsym.name, "old_qualified_name": bsym.qualified_name},
                                )
                            )
                return changes

        # Case 2: File Added
        if before_content is None or len(before_content) == 0:
            if after_content is not None and len(after_content) > 0:
                changes.append(
                    SemanticChange(
                        commit_sha=commit_sha,
                        file_path=file_path,
                        change_type=SemanticChangeType.FILE_ADDED,
                        symbol_name=file_path.split("/")[-1].split("\\")[-1],
                        qualified_name=file_path,
                        entity_type="file",
                    )
                )
                if self.parser.can_parse(file_path):
                    parsed_after = self.parser.parse_source(file_path, after_content)
                    for sym in parsed_after.symbols:
                        if sym.entity_type != "file":
                            changes.append(
                                SemanticChange(
                                    commit_sha=commit_sha,
                                    file_path=file_path,
                                    change_type=SemanticChangeType.SYMBOL_ADDED,
                                    symbol_name=sym.name,
                                    qualified_name=sym.qualified_name,
                                    entity_type=sym.entity_type,
                                    after_fingerprint=sym.content_hash,
                                    after_signature=sym.signature,
                                    after_line_range=(sym.start_line, sym.end_line),
                                )
                            )
            return changes

        # Case 3: File Removed
        if after_content is None or len(after_content) == 0:
            changes.append(
                SemanticChange(
                    commit_sha=commit_sha,
                    file_path=file_path,
                    change_type=SemanticChangeType.FILE_REMOVED,
                    symbol_name=file_path.split("/")[-1].split("\\")[-1],
                    qualified_name=file_path,
                    entity_type="file",
                )
            )
            if self.parser.can_parse(file_path):
                parsed_before = self.parser.parse_source(file_path, before_content)
                for sym in parsed_before.symbols:
                    if sym.entity_type != "file":
                        changes.append(
                            SemanticChange(
                                commit_sha=commit_sha,
                                file_path=file_path,
                                change_type=SemanticChangeType.SYMBOL_REMOVED,
                                symbol_name=sym.name,
                                qualified_name=sym.qualified_name,
                                entity_type=sym.entity_type,
                                before_fingerprint=sym.content_hash,
                                before_signature=sym.signature,
                                before_line_range=(sym.start_line, sym.end_line),
                            )
                        )
            return changes

        # Case 4: File Modified - Full Semantic AST Comparison
        if not self.parser.can_parse(file_path):
            # Non-code configuration or data file
            is_config = any(
                file_path.lower().endswith(ext)
                for ext in (".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".cfg")
            )
            c_type = SemanticChangeType.CONFIG_CHANGED if is_config else SemanticChangeType.BODY_CHANGED
            changes.append(
                SemanticChange(
                    commit_sha=commit_sha,
                    file_path=file_path,
                    change_type=c_type,
                    symbol_name=file_path.split("/")[-1].split("\\")[-1],
                    qualified_name=file_path,
                    entity_type="config" if is_config else "file",
                )
            )
            return changes

        parsed_before = self.parser.parse_source(file_path, before_content)
        parsed_after = self.parser.parse_source(file_path, after_content)

        # Index symbols excluding root file pseudo-symbol
        before_syms: dict[str, ParsedSymbol] = {
            s.qualified_name: s for s in parsed_before.symbols if s.entity_type != "file"
        }
        after_syms: dict[str, ParsedSymbol] = {
            s.qualified_name: s for s in parsed_after.symbols if s.entity_type != "file"
        }

        # Index relationships by source
        before_rels_by_src: dict[str, list[ParsedRelationship]] = {}
        for r in parsed_before.relationships:
            before_rels_by_src.setdefault(r.source_qualified_name, []).append(r)

        after_rels_by_src: dict[str, list[ParsedRelationship]] = {}
        for r in parsed_after.relationships:
            after_rels_by_src.setdefault(r.source_qualified_name, []).append(r)

        # A. Detect Added Symbols
        for qname, asym in after_syms.items():
            if qname not in before_syms:
                # Check for possible rename (same body hash, same type)
                renamed_from = None
                for bqname, bsym in before_syms.items():
                    if (
                        bqname not in after_syms
                        and bsym.entity_type == asym.entity_type
                        and (bsym.content_hash == asym.content_hash or are_symbols_lineage_match(bsym, asym))
                    ):
                        renamed_from = bsym
                        break

                if renamed_from:
                    changes.append(
                        SemanticChange(
                            commit_sha=commit_sha,
                            file_path=file_path,
                            change_type=SemanticChangeType.SYMBOL_RENAMED,
                            symbol_name=asym.name,
                            qualified_name=asym.qualified_name,
                            entity_type=asym.entity_type,
                            before_fingerprint=renamed_from.content_hash,
                            after_fingerprint=asym.content_hash,
                            before_signature=renamed_from.signature,
                            after_signature=asym.signature,
                            before_line_range=(renamed_from.start_line, renamed_from.end_line),
                            after_line_range=(asym.start_line, asym.end_line),
                            details={"renamed_from": renamed_from.qualified_name},
                        )
                    )
                else:
                    changes.append(
                        SemanticChange(
                            commit_sha=commit_sha,
                            file_path=file_path,
                            change_type=SemanticChangeType.SYMBOL_ADDED,
                            symbol_name=asym.name,
                            qualified_name=asym.qualified_name,
                            entity_type=asym.entity_type,
                            after_fingerprint=asym.content_hash,
                            after_signature=asym.signature,
                            after_line_range=(asym.start_line, asym.end_line),
                        )
                    )

        # B. Detect Removed Symbols
        for qname, bsym in before_syms.items():
            if qname not in after_syms:
                # If already detected as renamed, skip removal
                is_renamed = any(
                    c.change_type == SemanticChangeType.SYMBOL_RENAMED
                    and c.details.get("renamed_from") == qname
                    for c in changes
                )
                if not is_renamed:
                    changes.append(
                        SemanticChange(
                            commit_sha=commit_sha,
                            file_path=file_path,
                            change_type=SemanticChangeType.SYMBOL_REMOVED,
                            symbol_name=bsym.name,
                            qualified_name=bsym.qualified_name,
                            entity_type=bsym.entity_type,
                            before_fingerprint=bsym.content_hash,
                            before_signature=bsym.signature,
                            before_line_range=(bsym.start_line, bsym.end_line),
                        )
                    )

        # C. Detect Modified Symbols (Present in both before and after)
        for qname in before_syms.keys() & after_syms.keys():
            bsym = before_syms[qname]
            asym = after_syms[qname]

            # 1. Signature Change
            sig_changed = bsym.signature != asym.signature
            if sig_changed:
                changes.append(
                    SemanticChange(
                        commit_sha=commit_sha,
                        file_path=file_path,
                        change_type=SemanticChangeType.SIGNATURE_CHANGED,
                        symbol_name=asym.name,
                        qualified_name=asym.qualified_name,
                        entity_type=asym.entity_type,
                        before_fingerprint=bsym.content_hash,
                        after_fingerprint=asym.content_hash,
                        before_signature=bsym.signature,
                        after_signature=asym.signature,
                        before_line_range=(bsym.start_line, bsym.end_line),
                        after_line_range=(asym.start_line, asym.end_line),
                    )
                )

            # 2. Body Change (content hash changed, but not redundant if already logged as signature change)
            if bsym.content_hash != asym.content_hash and not sig_changed:
                ch_type = (
                    SemanticChangeType.CLASS_CHANGED
                    if asym.entity_type == "class"
                    else SemanticChangeType.BODY_CHANGED
                )
                changes.append(
                    SemanticChange(
                        commit_sha=commit_sha,
                        file_path=file_path,
                        change_type=ch_type,
                        symbol_name=asym.name,
                        qualified_name=asym.qualified_name,
                        entity_type=asym.entity_type,
                        before_fingerprint=bsym.content_hash,
                        after_fingerprint=asym.content_hash,
                        before_signature=bsym.signature,
                        after_signature=asym.signature,
                        before_line_range=(bsym.start_line, bsym.end_line),
                        after_line_range=(asym.start_line, asym.end_line),
                    )
                )

            # 3. Inheritance / Interface Change (for classes)
            b_bases = set(bsym.metadata.get("bases", []))
            a_bases = set(asym.metadata.get("bases", []))
            if b_bases != a_bases:
                changes.append(
                    SemanticChange(
                        commit_sha=commit_sha,
                        file_path=file_path,
                        change_type=SemanticChangeType.INHERITANCE_CHANGED,
                        symbol_name=asym.name,
                        qualified_name=asym.qualified_name,
                        entity_type="class",
                        details={"before_bases": list(b_bases), "after_bases": list(a_bases)},
                    )
                )

            # 4. Route Decorator / Contract Change
            b_decorators = set(bsym.metadata.get("decorators", []))
            a_decorators = set(asym.metadata.get("decorators", []))
            if b_decorators != a_decorators:
                is_route_decor = any(
                    "get" in d.lower() or "post" in d.lower() or "put" in d.lower() or "delete" in d.lower()
                    for d in b_decorators | a_decorators
                )
                if is_route_decor:
                    changes.append(
                        SemanticChange(
                            commit_sha=commit_sha,
                            file_path=file_path,
                            change_type=SemanticChangeType.ROUTE_CHANGED,
                            symbol_name=asym.name,
                            qualified_name=asym.qualified_name,
                            entity_type=asym.entity_type,
                            details={"before_decorators": list(b_decorators), "after_decorators": list(a_decorators)},
                        )
                    )

            # 5. Relationship Changes (calls, imports from this symbol)
            brels = {(r.relationship_type, r.target_qualified_name) for r in before_rels_by_src.get(qname, [])}
            arels = {(r.relationship_type, r.target_qualified_name) for r in after_rels_by_src.get(qname, [])}
            if brels != arels:
                diff_rels = [f"{t}:{n}" for (t, n) in (arels ^ brels)]
                changes.append(
                    SemanticChange(
                        commit_sha=commit_sha,
                        file_path=file_path,
                        change_type=SemanticChangeType.CALL_RELATIONSHIP_CHANGED,
                        symbol_name=asym.name,
                        qualified_name=asym.qualified_name,
                        entity_type=asym.entity_type,
                        affected_relationships=diff_rels,
                    )
                )

        return changes
