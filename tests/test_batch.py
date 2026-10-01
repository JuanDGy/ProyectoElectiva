import pytest

import main


def test_batch_stops_at_requested_limit(monkeypatch):
    results = iter([0, 0, 0, 1])
    monkeypatch.setattr(main, 'run_next', lambda: next(results))
    assert main.run_batch(3) == 0
    assert next(results) == 1


def test_all_stops_when_queue_is_empty(monkeypatch):
    results = iter([0, 0, None])
    monkeypatch.setattr(main, 'run_next', lambda: next(results))
    assert main.run_batch(None) == 0


def test_limit_stops_early_when_queue_is_empty(monkeypatch):
    results = iter([0, None])
    monkeypatch.setattr(main, 'run_next', lambda: next(results))
    assert main.run_batch(10) == 0


def test_error_stops_batch_without_retrying_same_file(monkeypatch):
    results = iter([0, 1, 0])
    monkeypatch.setattr(main, 'run_next', lambda: next(results))
    assert main.run_batch(None) == 1
    assert next(results) == 0


@pytest.mark.parametrize('limit', [0, -1])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError):
        main.run_batch(limit)


@pytest.mark.parametrize('arguments,expected', [([], 1), (['--limit', '5'], 5), (['--all'], None)])
def test_cli_passes_requested_batch_size(monkeypatch, arguments, expected):
    monkeypatch.setattr(main.sys, 'argv', ['main.py', *arguments])
    received = []
    def batch(limit):
        received.append(limit)
        return 0
    monkeypatch.setattr(main, 'run_batch', batch)
    assert main.cli() == 0
    assert received == [expected]


@pytest.mark.parametrize('arguments', [['--limit', '0'], ['--limit', '-2'], ['--limit', 'abc'], ['--all', '--limit', '3'], ['--all', '--file-id', 'id']])
def test_cli_rejects_invalid_or_conflicting_options(monkeypatch, arguments):
    monkeypatch.setattr(main.sys, 'argv', ['main.py', *arguments])
    with pytest.raises(SystemExit) as error:
        main.cli()
    assert error.value.code == 2
