"""Unit tests for ASTSemanticDiffer."""

from cortexforge.code_intelligence.treesitter.semantic_diff import (
    ASTSemanticDiffer,
    SemanticChangeType,
)


def test_detect_symbol_added_and_removed():
    differ = ASTSemanticDiffer()

    before_py = b"""
def existing_func(a: int) -> int:
    return a * 2

def old_func() -> str:
    return "legacy"
"""

    after_py = b"""
def existing_func(a: int) -> int:
    return a * 2

def new_func() -> str:
    return "modern"
"""

    changes = differ.diff_file_contents("services/calc.py", before_py, after_py)
    change_types = [c.change_type for c in changes]
    symbols = [c.symbol_name for c in changes]

    assert SemanticChangeType.SYMBOL_REMOVED in change_types
    assert SemanticChangeType.SYMBOL_ADDED in change_types
    assert "old_func" in symbols
    assert "new_func" in symbols

    # Crucial Invariant: existing_func was NOT modified, so it should NOT appear in changes
    assert "existing_func" not in symbols


def test_detect_signature_changed():
    differ = ASTSemanticDiffer()

    before_py = b"""
def authenticate(username: str) -> bool:
    return True
"""

    after_py = b"""
def authenticate(username: str, tenant_id: str = "default") -> bool:
    return True
"""

    changes = differ.diff_file_contents("services/auth.py", before_py, after_py)
    assert len(changes) == 1
    assert changes[0].change_type == SemanticChangeType.SIGNATURE_CHANGED
    assert changes[0].symbol_name == "authenticate"
    assert "tenant_id" in (changes[0].after_signature or "")


def test_detect_body_changed_only():
    differ = ASTSemanticDiffer()

    before_py = b"""
def calculate_tax(amount: float) -> float:
    return amount * 0.15
"""

    after_py = b"""
def calculate_tax(amount: float) -> float:
    rate = 0.20
    return amount * rate
"""

    changes = differ.diff_file_contents("services/tax.py", before_py, after_py)
    assert len(changes) == 1
    assert changes[0].change_type == SemanticChangeType.BODY_CHANGED
    assert changes[0].symbol_name == "calculate_tax"
    assert changes[0].before_fingerprint != changes[0].after_fingerprint


def test_detect_class_inheritance_change():
    differ = ASTSemanticDiffer()

    before_py = b"""
class BaseService:
    pass

class OrderService(BaseService):
    def run(self):
        pass
"""

    after_py = b"""
class BaseService:
    pass

class AsyncService:
    pass

class OrderService(BaseService, AsyncService):
    def run(self):
        pass
"""

    changes = differ.diff_file_contents("services/orders.py", before_py, after_py)
    change_types = [c.change_type for c in changes]
    assert SemanticChangeType.INHERITANCE_CHANGED in change_types
    assert SemanticChangeType.SYMBOL_ADDED in change_types  # AsyncService added
