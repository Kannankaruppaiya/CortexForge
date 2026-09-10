"""Dynamic test execution and output parsing adapters for real test intelligence.

Parses native structured outputs from test runners (Vitest, Pytest, JUnit XML)
into strongly typed TestExecutionResult objects without synthetic injection.
"""

import asyncio
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.failure_intelligence import FailureIntelligenceEngine
from cortexforge.agent.test_intelligence import (
    TestIntelligenceEngine,
    TestIntelligenceReport,
)
from cortexforge.core.models import TestCaseResult, TestRun

logger = logging.getLogger(__name__)


@dataclass
class ParsedTestCase:
    """A single test case outcome parsed from actual runner output."""

    test_name: str
    status: str  # "PASSED", "FAILED", "SKIPPED", "ERROR"
    duration_ms: float = 0.0
    error_message: str | None = None
    stack_trace: str | None = None
    affected_files: list[str] = field(default_factory=list)


@dataclass
class TestExecutionResult:
    """Aggregate parsed execution result from a test run."""

    framework: str
    exit_code: int
    duration_ms: float
    passed_count: int
    failed_count: int
    skipped_count: int
    test_cases: list[ParsedTestCase]
    raw_stdout: str
    raw_stderr: str


class TestFrameworkAdapter(ABC):
    """Abstract adapter for runner output parsing."""

    framework_name: str = "generic"

    @abstractmethod
    def can_parse(self, stdout: str, stderr: str, command: str) -> bool:
        """Check if this adapter can parse the output or command."""
        ...

    @abstractmethod
    def parse_execution(
        self, stdout: str, stderr: str, exit_code: int, duration_ms: float
    ) -> TestExecutionResult:
        """Parse raw runner stdout/stderr into structured TestExecutionResult."""
        ...


class VitestAdapter(TestFrameworkAdapter):
    """Parses Vitest test output (both JSON and standard terminal output)."""

    framework_name: str = "vitest"

    def can_parse(self, stdout: str, stderr: str, command: str) -> bool:
        cmd_lower = command.lower()
        return (
            "vitest" in cmd_lower
            or '"numTotalTestSuites"' in stdout
            or '"testResults"' in stdout
            or (
                ("✓" in stdout or "×" in stdout or "FAIL" in stdout)
                and "vitest" in stdout.lower()
            )
        )

    def parse_execution(
        self, stdout: str, stderr: str, exit_code: int, duration_ms: float
    ) -> TestExecutionResult:
        # 1. Try parsing structured JSON
        cleaned = stdout.strip()
        if cleaned.startswith("{") and cleaned.endswith("}"):
            try:
                data = json.loads(cleaned)
                return self._parse_json(data, stdout, stderr, exit_code, duration_ms)
            except Exception as e:
                logger.debug("Vitest direct JSON parse failed: %s", e)

        start = stdout.find("{")
        end = stdout.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(stdout[start : end + 1])
                return self._parse_json(data, stdout, stderr, exit_code, duration_ms)
            except Exception as e:
                logger.debug("Vitest substring JSON parse failed: %s", e)

        # 2. Fall back to parsing standard terminal output
        return self._parse_terminal(stdout, stderr, exit_code, duration_ms)

    def _parse_json(
        self,
        data: dict[str, Any],
        stdout: str,
        stderr: str,
        exit_code: int,
        duration_ms: float,
    ) -> TestExecutionResult:
        test_cases: list[ParsedTestCase] = []
        passed = 0
        failed = 0
        skipped = 0

        for suite in data.get("testResults", []):
            suite_file = suite.get("name", "")
            # Canonicalize file path
            suite_file = (
                suite_file.replace("\\", "/").split("/")[-1] if suite_file else ""
            )
            for assertion in suite.get("assertionResults", []):
                title = assertion.get("title", "")
                full_name = f"{suite_file} > {title}" if suite_file else title
                raw_status = assertion.get("status", "").lower()
                dur = float(assertion.get("duration", 0.0) or 0.0)
                err_msgs = assertion.get("failureMessages", [])
                err_str = "\n".join(err_msgs) if err_msgs else None

                if raw_status in ("passed", "pass"):
                    st = "PASSED"
                    passed += 1
                elif raw_status in ("failed", "fail"):
                    st = "FAILED"
                    failed += 1
                elif raw_status in ("skipped", "pending", "todo"):
                    st = "SKIPPED"
                    skipped += 1
                else:
                    st = "ERROR"
                    failed += 1

                test_cases.append(
                    ParsedTestCase(
                        test_name=full_name,
                        status=st,
                        duration_ms=dur,
                        error_message=err_str[:500] if err_str else None,
                        stack_trace=err_str,
                        affected_files=[suite_file] if suite_file else [],
                    )
                )

        return TestExecutionResult(
            framework="vitest",
            exit_code=exit_code,
            duration_ms=duration_ms,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            test_cases=test_cases,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )

    def _parse_terminal(
        self, stdout: str, stderr: str, exit_code: int, duration_ms: float
    ) -> TestExecutionResult:
        test_cases: list[ParsedTestCase] = []
        passed = 0
        failed = 0
        skipped = 0

        # Pattern matches lines like:
        #   ✓ tests/sanity.test.ts > basic sanity check
        #   × tests/sanity.test.ts > failed check
        for line in stdout.splitlines():
            line_str = line.strip()
            if "✓" in line_str or "[PASS]" in line_str:
                # Passed test
                name = re.sub(r"^[✓\s\[PASS\]]+", "", line_str).strip()
                file_match = re.search(r"([\w\.\-/]+\.(?:test|spec)\.[a-zA-Z]+)", name)
                aff_files = [file_match.group(1)] if file_match else []
                test_cases.append(
                    ParsedTestCase(
                        test_name=name,
                        status="PASSED",
                        affected_files=aff_files,
                    )
                )
                passed += 1
            elif "×" in line_str or "[FAIL]" in line_str:
                # Failed test
                name = re.sub(r"^[×\s\[FAIL\]]+", "", line_str).strip()
                file_match = re.search(r"([\w\.\-/]+\.(?:test|spec)\.[a-zA-Z]+)", name)
                aff_files = [file_match.group(1)] if file_match else []
                test_cases.append(
                    ParsedTestCase(
                        test_name=name,
                        status="FAILED",
                        error_message=f"Test failure in {name}",
                        affected_files=aff_files,
                    )
                )
                failed += 1
            elif "↓" in line_str or "[SKIP]" in line_str:
                name = re.sub(r"^[↓\s\[SKIP\]]+", "", line_str).strip()
                test_cases.append(
                    ParsedTestCase(
                        test_name=name,
                        status="SKIPPED",
                    )
                )
                skipped += 1

        return TestExecutionResult(
            framework="vitest",
            exit_code=exit_code,
            duration_ms=duration_ms,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            test_cases=test_cases,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )


class PytestAdapter(TestFrameworkAdapter):
    """Parses Pytest output (standard terminal output and JSON)."""

    framework_name: str = "pytest"

    def can_parse(self, stdout: str, stderr: str, command: str) -> bool:
        cmd_lower = command.lower()
        return (
            "pytest" in cmd_lower
            or "py.test" in cmd_lower
            or "== test session starts ==" in stdout
            or "=== FAILURES ===" in stdout
        )

    def parse_execution(
        self, stdout: str, stderr: str, exit_code: int, duration_ms: float
    ) -> TestExecutionResult:
        test_cases: list[ParsedTestCase] = []
        passed = 0
        failed = 0
        skipped = 0

        # Pattern: tests/test_foo.py::test_name PASSED [ 50%]
        pattern = re.compile(
            r"^(.*?::.*?)\s+(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)", re.MULTILINE
        )
        for match in pattern.finditer(stdout):
            t_name = match.group(1).strip()
            raw_status = match.group(2).strip()
            file_part = t_name.split("::")[0].strip()

            if raw_status in ("PASSED", "XPASS"):
                st = "PASSED"
                passed += 1
            elif raw_status in ("FAILED", "ERROR"):
                st = "FAILED"
                failed += 1
            else:
                st = "SKIPPED"
                skipped += 1

            test_cases.append(
                ParsedTestCase(
                    test_name=t_name,
                    status=st,
                    affected_files=[file_part] if file_part else [],
                )
            )

        return TestExecutionResult(
            framework="pytest",
            exit_code=exit_code,
            duration_ms=duration_ms,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            test_cases=test_cases,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )


class JUnitXMLAdapter(TestFrameworkAdapter):
    """Parses standard JUnit XML reports."""

    framework_name: str = "junit_xml"

    def can_parse(self, stdout: str, stderr: str, command: str) -> bool:
        return "<testsuite" in stdout or "<testsuites" in stdout

    def parse_execution(
        self, stdout: str, stderr: str, exit_code: int, duration_ms: float
    ) -> TestExecutionResult:
        test_cases: list[ParsedTestCase] = []
        passed = 0
        failed = 0
        skipped = 0

        try:
            root = ET.fromstring(stdout)
            suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
            for suite in suites:
                for case in suite.findall("testcase"):
                    classname = case.get("classname", "")
                    name = case.get("name", "")
                    full_name = f"{classname}::{name}" if classname else name
                    time_val = float(case.get("time", "0.0") or 0.0) * 1000.0

                    failure = case.find("failure")
                    error = case.find("error")
                    skipped_node = case.find("skipped")

                    if failure is not None:
                        st = "FAILED"
                        failed += 1
                        msg = failure.get("message", "")
                        trace = failure.text
                    elif error is not None:
                        st = "FAILED"
                        failed += 1
                        msg = error.get("message", "")
                        trace = error.text
                    elif skipped_node is not None:
                        st = "SKIPPED"
                        skipped += 1
                        msg = None
                        trace = None
                    else:
                        st = "PASSED"
                        passed += 1
                        msg = None
                        trace = None

                    test_cases.append(
                        ParsedTestCase(
                            test_name=full_name,
                            status=st,
                            duration_ms=time_val,
                            error_message=msg,
                            stack_trace=trace,
                        )
                    )
        except Exception as e:
            logger.debug("JUnit XML parse failed: %s", e)

        return TestExecutionResult(
            framework="junit_xml",
            exit_code=exit_code,
            duration_ms=duration_ms,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            test_cases=test_cases,
            raw_stdout=stdout,
            raw_stderr=stderr,
        )


class TestExecutionPipeline:
    """Executes test commands, selects parsing adapter, and records genuine test runs."""

    __test__ = False

    def __init__(self) -> None:
        self.adapters: list[TestFrameworkAdapter] = [
            VitestAdapter(),
            PytestAdapter(),
            JUnitXMLAdapter(),
        ]
        self.failure_engine = FailureIntelligenceEngine()
        self.intelligence_engine = TestIntelligenceEngine()

    def select_adapter(
        self, stdout: str, stderr: str, command: str
    ) -> TestFrameworkAdapter:
        for adapter in self.adapters:
            if adapter.can_parse(stdout, stderr, command):
                return adapter
        # Default to Vitest if unspecified or generic
        return self.adapters[0]

    async def execute_and_record(
        self,
        session: AsyncSession,
        project_id: str,
        command: str | list[str],
        cwd: str,
        task_id: str | None = None,
        change_set_id: str | None = None,
    ) -> tuple[TestRun, list[TestCaseResult], TestIntelligenceReport]:
        """Execute test command, dynamically parse output, and persist test intelligence."""
        cmd_str = command if isinstance(command, str) else " ".join(command)

        start_time = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        duration_ms = (time.monotonic() - start_time) * 1000.0

        raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
        raw_stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        adapter = self.select_adapter(raw_stdout, raw_stderr, cmd_str)
        parsed = adapter.parse_execution(raw_stdout, raw_stderr, exit_code, duration_ms)

        overall_status = (
            "PASSED" if exit_code == 0 and parsed.failed_count == 0 else "FAILED"
        )

        # Persist TestRun
        test_run = TestRun(
            project_id=project_id,
            commit_sha="HEAD",
            framework=adapter.framework_name,
            status=overall_status,
            total_tests=len(parsed.test_cases),
            passed_count=parsed.passed_count,
            failed_count=parsed.failed_count,
            duration_ms=duration_ms,
        )
        session.add(test_run)
        await session.flush()

        results: list[TestCaseResult] = []
        for tc in parsed.test_cases:
            res = TestCaseResult(
                test_run_id=test_run.id,
                test_name=tc.test_name,
                status=tc.status,
                duration_ms=tc.duration_ms,
                error_message=tc.error_message,
                stack_trace=tc.stack_trace,
                affected_files=tc.affected_files,
            )
            session.add(res)
            results.append(res)

        await session.flush()

        # Attribute the run
        report = await self.intelligence_engine.attribute_run(
            session=session,
            test_run_id=test_run.id,
            change_set_id=change_set_id,
        )

        await session.commit()
        return test_run, results, report
