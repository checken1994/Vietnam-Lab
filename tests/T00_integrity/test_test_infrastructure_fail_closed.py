"""Regression tests for verification infrastructure.

Infrastructure failure/unknown must never be reported as PASS, and a deny-egress
runtime must not create hidden network traffic from test or model metadata paths.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import pytest
from scripts import mutation_engine
from scripts import scp_soak_test


def test_mutation_missing_target_is_error():
    with pytest.raises(FileNotFoundError):
        mutation_engine.run_mutation_tests('scp/does_not_exist.py',
            'tests/T00_integrity/test_test_infrastructure_fail_closed.py')


def test_mutation_unsupported_target_is_error(tmp_path):
    target = tmp_path / 'not_python.txt'
    target.write_text('hello', encoding='utf-8')
    with pytest.raises(ValueError):
        mutation_engine.run_mutation_tests(str(target),
            'tests/T00_integrity/test_test_infrastructure_fail_closed.py')


def test_mutation_zero_mutants_is_error(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    target_dir = Path(tempfile.mkdtemp(prefix='temp_mutation_target_'))
    try:
        target = target_dir / 'target.py'
        target.write_text('x = object()\n', encoding='utf-8')
        shutil.copy2(Path(__file__), target_dir / Path(__file__).name)
        monkeypatch.chdir(target_dir)
        monkeypatch.setattr(mutation_engine, 'generate_mutants', lambda
            _path: [])
        with pytest.raises(RuntimeError, match='no supported mutants'):
            mutation_engine.run_mutation_tests(str(target),
                'test_test_infrastructure_fail_closed.py')
    finally:
        os.chdir(repo_root)
        shutil.rmtree(target_dir, ignore_errors=True)


def test_mutation_pytest_collection_error_is_not_counted_as_kill(monkeypatch):
    repo_root = Path(__file__).resolve().parents[2]
    target_dir = Path(tempfile.mkdtemp(prefix='temp_mutation_target_2_'))
    try:
        target = target_dir / 'target.py'
        target.write_text('x = 1\n', encoding='utf-8')
        shutil.copy2(Path(__file__), target_dir / Path(__file__).name)
        monkeypatch.chdir(target_dir)
        monkeypatch.setattr(mutation_engine, 'generate_mutants', lambda
            _path: [(1, 'x = 2\n')])
        monkeypatch.setattr(mutation_engine.subprocess, 'run', lambda *
            _args, **_kwargs: subprocess.CompletedProcess([], 2, stdout=b'',
            stderr=b'collection error'))
        with pytest.raises(RuntimeError) as exc_info:
            mutation_engine.run_mutation_tests(str(target),
                'test_test_infrastructure_fail_closed.py')
        message = str(exc_info.value)
        assert 'pytest code 2' in message
        assert 'collection error' in message
        assert target.read_text(encoding='utf-8') == 'x = 1\n'
    finally:
        os.chdir(repo_root)
        shutil.rmtree(target_dir, ignore_errors=True)


def test_soak_workload_nonzero_exit_is_recorded(monkeypatch):
    monkeypatch.setattr(scp_soak_test.subprocess, 'run', lambda *_args, **
        _kwargs: subprocess.CompletedProcess([], 3, stdout='', stderr='boom'))
    results = {}
    scp_soak_test.run_workload(7, results)
    assert results[7]['returncode'] == 3
    assert 'boom' in results[7]['stderr']


def test_soak_workload_timeout_is_recorded(monkeypatch):

    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd='python', timeout=5)
    monkeypatch.setattr(scp_soak_test.subprocess, 'run', _timeout)
    results = {}
    scp_soak_test.run_workload(8, results)
    assert results[8]['timeout'] is True


def test_zero_duration_soak_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(scp_soak_test, 'ROOT', tmp_path)
    assert scp_soak_test.soak_loop(0) is False
