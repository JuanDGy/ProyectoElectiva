"""El flujo siempre sincroniza la pestaña correcta antes de los gráficos."""
import subprocess

import pytest

from ml.workflow import build_steps, update_predictions


def test_preview_never_writes_to_sheets():
    steps = build_steps(year=2026, month=2)
    assert len(steps) == 1
    assert steps[0][1] == "ml.publish"
    assert steps[0][2] == [
        "--model-path", "auto", "--year", "2026", "--month", "2",
    ]


def test_normal_mode_syncs_predictions_then_charts():
    steps = build_steps(publish=True, year=2026, month=2)
    assert [mod for _, mod, _ in steps] == [
        "ml.publish", "ml.sheets_dashboard",
    ]
    assert steps[0][2] == [
        "--model-path", "auto", "--year", "2026", "--month", "2",
        "--write", "--sync",
    ]
    assert steps[1][2] == ["--write", "--year", "2026", "--month", "2"]


def test_full_mode_executes_snapshot_before_training_and_sync():
    steps = build_steps(complete=True, publish=True)
    assert [mod for _, mod, _ in steps] == [
        "ml.snapshot", "scripts.experiment_models", "ml.train_auto",
        "ml.publish", "ml.sheets_dashboard",
    ]
    assert "--write" in steps[-2][2]
    assert "--sync" in steps[-2][2]


def test_no_charts_option():
    steps = build_steps(publish=True, charts=False)
    assert len(steps) == 1
    assert steps[0][1] == "ml.publish"


def test_incomplete_or_invalid_period_rejected():
    with pytest.raises(ValueError, match="juntos"):
        build_steps(year=2026)
    with pytest.raises(ValueError, match="juntos"):
        build_steps(month=2)
    with pytest.raises(ValueError, match="entre 1 y 12"):
        build_steps(year=2026, month=13)


def test_runner_stops_before_sheets_if_training_fails():
    executed = []

    def runner(command, **kwargs):
        executed.append(command[2])
        if command[2] == "ml.train_auto":
            raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        update_predictions(complete=True, publish=True, runner=runner)
    assert executed == [
        "ml.snapshot", "scripts.experiment_models", "ml.train_auto",
    ]


def test_runner_clearly_targets_prediction_sheet_and_preview(capsys):
    executed = []
    def runner(command, **kwargs):
        executed.append(command)

    update_predictions(year=2026, month=2, runner=runner)
    printed = capsys.readouterr().out
    assert "Predicciones_Cuenta" in printed
    assert "Modo lectura" in printed
    assert len(executed) == 1
