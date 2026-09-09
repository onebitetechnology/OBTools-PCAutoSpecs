import pytest
from hardware_classification import classify_display_connection, normalize_panel_size


@pytest.mark.parametrize('technology', [6, 11, 13, 2147483648, -2147483648])
def test_internal(technology):
    assert classify_display_connection(technology).role == 'internal'


@pytest.mark.parametrize('technology', [0, 1, 2, 3, 4, 5, 8, 9, 10, 12, 14, 15, 16])
def test_external(technology):
    assert classify_display_connection(technology).role == 'external'


@pytest.mark.parametrize('technology', [-2, -1, 4294967294, 4294967295, None, 'broken', 7, 99, 11.4, True])
def test_unknown(technology):
    assert classify_display_connection(technology).role == 'unknown'


@pytest.mark.parametrize('exact,nominal', [(13.3,13.3),(14,14),(15.6,15.6),(16,16),(16.3,16),(17.3,17.3),(30, None)])
def test_size(exact,nominal):
    size = normalize_panel_size(exact)
    assert (size.exact_inches,size.nominal_inches) == (exact,nominal)
    if exact == 16.3:
        assert size.display_label == '16.3 in measured (16-inch class)'


@pytest.mark.parametrize('value', [None, 0, -1, float('nan'), float('inf')])
def test_unavailable_size(value):
    assert normalize_panel_size(value).exact_inches is None
