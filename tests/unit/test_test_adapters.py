"""Tests for dynamic test execution adapters without synthetic injection."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortexforge.agent.test_adapters import (
    JUnitXMLAdapter,
    PytestAdapter,
    TestExecutionPipeline,
    VitestAdapter,
)
from cortexforge.core.models import Project


def test_vitest_json_adapter_parsing():
    """Verify VitestAdapter dynamically parses machine-readable Vitest JSON."""
    adapter = VitestAdapter()

    sample_json = """
    {
      "numTotalTestSuites": 1,
      "numPassedTestSuites": 0,
      "numFailedTestSuites": 1,
      "testResults": [
        {
          "name": "src/auth/login.test.ts",
          "status": "failed",
          "assertionResults": [
            {
              "title": "should authenticate valid bearer token",
              "status": "passed",
              "duration": 42.5,
              "failureMessages": []
            },
            {
              "title": "should reject expired session token",
              "status": "failed",
              "duration": 18.2,
              "failureMessages": ["AssertionError: expected 401 but got 500\\n  at src/auth/login.test.ts:35:12"]
            }
          ]
        }
      ]
    }
    """

    res = adapter.parse_execution(sample_json, "", exit_code=1, duration_ms=60.7)
    assert res.framework == "vitest"
    assert res.passed_count == 1
    assert res.failed_count == 1
    assert len(res.test_cases) == 2

    c1, c2 = res.test_cases
    assert c1.status == "PASSED"
    assert c1.duration_ms == 42.5
    assert "login.test.ts" in c1.affected_files[0]

    assert c2.status == "FAILED"
    assert "AssertionError" in c2.error_message
    assert "login.test.ts:35:12" in c2.stack_trace


def test_vitest_terminal_adapter_parsing():
    """Verify VitestAdapter parses standard terminal reporter output."""
    adapter = VitestAdapter()
    sample_term = """
    ✓ tests/math.test.ts > adds two numbers correctly
    × tests/auth.spec.ts > validates secure cookie
    ↓ tests/payments.test.ts > stripe webhook charge
    """
    res = adapter.parse_execution(sample_term, "", exit_code=1, duration_ms=120.0)
    assert res.passed_count == 1
    assert res.failed_count == 1
    assert res.skipped_count == 1
    assert len(res.test_cases) == 3


def test_pytest_adapter_parsing():
    """Verify PytestAdapter parses standard pytest terminal output."""
    adapter = PytestAdapter()
    sample_pytest = """
    tests/unit/test_auth.py::test_login_success PASSED               [ 50%]
    tests/unit/test_auth.py::test_login_invalid_password FAILED      [100%]
    """
    res = adapter.parse_execution(sample_pytest, "", exit_code=1, duration_ms=450.0)
    assert res.passed_count == 1
    assert res.failed_count == 1
    assert len(res.test_cases) == 2
    assert res.test_cases[0].status == "PASSED"
    assert res.test_cases[1].status == "FAILED"
    assert res.test_cases[0].affected_files == ["tests/unit/test_auth.py"]


def test_junit_xml_adapter_parsing():
    """Verify JUnitXMLAdapter parses standard XML test reports."""
    adapter = JUnitXMLAdapter()
    sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <testsuite name="Vitest Tests" tests="2" failures="1" errors="0" time="0.123">
        <testcase classname="tests/sanity.test.ts" name="sanity check" time="0.045"/>
        <testcase classname="tests/sanity.test.ts" name="broken check" time="0.078">
            <failure message="Expected true but got false">Error details</failure>
        </testcase>
    </testsuite>
    """
    res = adapter.parse_execution(sample_xml, "", exit_code=1, duration_ms=123.0)
    assert res.passed_count == 1
    assert res.failed_count == 1
    assert len(res.test_cases) == 2
    assert res.test_cases[0].status == "PASSED"
    assert res.test_cases[1].status == "FAILED"
    assert "Expected true but got false" in res.test_cases[1].error_message


@pytest.mark.asyncio
async def test_execution_pipeline_real_command(test_session: AsyncSession, tmp_path):
    """Verify TestExecutionPipeline actually executes real command and persists intelligence."""
    proj = Project(name="LivePipelineProj", local_path=str(tmp_path), status="READY")
    test_session.add(proj)
    await test_session.commit()

    pipeline = TestExecutionPipeline()
    # Execute actual Python pytest on a fast unit test
    run, results, report = await pipeline.execute_and_record(
        session=test_session,
        project_id=proj.id,
        command="uv run pytest tests/unit/test_capability_registry.py -v",
        cwd=".",
    )

    assert run is not None
    assert run.id is not None
    assert run.status == "PASSED"
    assert run.passed_count > 0
    assert len(results) > 0
    assert report is not None
