import pytest
from dab_dlt_learning import taxis

@pytest.mark.integration
def test_find_all_taxis():
    results = taxis.find_all_taxis()
    assert results.count() > 5