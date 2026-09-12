"""Project integrity checks: does CortexForge's own documentation match itself?

Specification section 46. The system is built on the premise that documentation is
untrusted and drift between what a project says and what it does is worth
detecting. Applying that to CortexForge itself is the cheapest possible test of
whether the premise is real.
"""

from cortexforge.integrity.checker import (
    IntegrityFinding,
    IntegrityReport,
    ProjectIntegrityChecker,
)

__all__ = ["IntegrityFinding", "IntegrityReport", "ProjectIntegrityChecker"]
