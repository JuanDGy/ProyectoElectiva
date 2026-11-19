"""Un único proceso local puede aplicar facturas al mismo tiempo."""
import pytest

from pipeline.pending import exclusive_apply_lock


def test_exclusive_lock_blocks_a_second_process(tmp_path):
    path = tmp_path / "apply.lock"
    with exclusive_apply_lock(path):
        assert path.exists()
        with pytest.raises(FileExistsError):
            with exclusive_apply_lock(path):
                pass
        assert path.exists()
    assert not path.exists()


def test_lock_released_after_exception(tmp_path):
    path = tmp_path / "apply.lock"
    with pytest.raises(RuntimeError):
        with exclusive_apply_lock(path):
            raise RuntimeError("fallo de operación")
    assert not path.exists()
    with exclusive_apply_lock(path):
        assert path.exists()
