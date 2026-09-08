"""Code intelligence abstractions and symbol structures."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedSymbol:
    entity_type: str  # file, module, class, interface, function, method, variable, api, model, test
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None = None
    content_hash: str = ""
    language: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedRelationship:
    source_qualified_name: str
    target_qualified_name: str
    relationship_type: str  # imports, calls, inherits, implements, depends_on, tests, routes_to, uses, contains
    confidence: float = 1.0
    source: str = "tree_sitter"


@dataclass
class ParseResult:
    symbols: list[ParsedSymbol] = field(default_factory=list)
    relationships: list[ParsedRelationship] = field(default_factory=list)
    language: str = "unknown"
    error: str | None = None


class CodeIntelligenceProvider(ABC):
    """Abstract interface for code parsing and semantic symbol extraction."""

    @abstractmethod
    def can_parse(self, file_path: str) -> bool:
        """Return True if this provider can parse the given file."""

    @abstractmethod
    def parse_source(self, file_path: str, content: bytes) -> ParseResult:
        """Parse source code bytes and return extracted symbols and relationships."""
