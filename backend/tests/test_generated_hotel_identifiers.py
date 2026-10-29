import pytest

from app.contracts.identifiers import canonical_hotel_id


@pytest.mark.parametrize("code", ["CMB", "LED", "PKC", "NRT"])
def test_fictional_identity_has_its_own_canonical_namespace(code):
    assert canonical_hotel_id(f"demo:hotel/{code}-01") == f"demo:hotel/{code}-01"


@pytest.mark.parametrize(
    "value",
    [
        "demo:hotel/../../key",
        "demo:hotel/CMB-00",
        "demo:hotel/cmb-01",
        "demo:hotel/CMB-01/extra",
        "demo:hotel/CMB-01%2fkey",
        "demo:hotel/CMB-01\n",
        "demo:hotel/CMB-0001",
        "other/provider/path",
    ],
)
def test_new_identity_does_not_allow_other_filesystem_paths(value):
    with pytest.raises(ValueError):
        canonical_hotel_id(value)
