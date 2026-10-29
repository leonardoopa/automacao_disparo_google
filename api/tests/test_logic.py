from app.collector import to_lead
from app.geo import grid_cells, haversine_km
from app.phones import normalize_phone

ORIGIN = (41.3605573, 2.1255221)


def row(**kw):
    base = {
        "title": "Super Test",
        "phone": "612 34 56 78",
        "latitude": "41.37",
        "longitude": "2.13",
        "place_id": "p1",
        "category": "Supermercado",
        "address": "Carrer X",
        "website": "",
        "emails": "a@x.es; b@x.es",
        "link": "https://maps.google.com/x",
        "review_rating": "4.2",
        "review_count": "10",
    }
    base.update(kw)
    return base


def test_haversine():
    assert haversine_km(*ORIGIN, *ORIGIN) == 0
    assert abs(haversine_km(0, 0, 0, 1) - 111.2) < 0.5


def test_grid_3x3_covers_radius():
    cells = grid_cells(*ORIGIN, 10, 3)
    assert len(cells) == 9
    for c in cells:
        assert isinstance(c["lat"], str) and isinstance(c["lon"], str)
        assert haversine_km(*ORIGIN, float(c["lat"]), float(c["lon"])) < 10 * 1.5
        assert 11 <= c["zoom"] <= 17


def test_grid_1x1_is_origin():
    (c,) = grid_cells(*ORIGIN, 10, 1)
    assert haversine_km(*ORIGIN, float(c["lat"]), float(c["lon"])) < 0.01


def test_phone():
    assert normalize_phone("612 34 56 78") == ("+34612345678", True)
    assert normalize_phone("+34 933 37 12 34") == ("+34933371234", False)
    assert normalize_phone("") == (None, False)
    assert normalize_phone("123") == (None, False)


def test_to_lead_inside_radius():
    lead = to_lead(row(), ORIGIN, 10)
    assert lead["place_id"] == "p1"
    assert lead["phone_e164"] == "+34612345678" and lead["is_mobile"]
    assert lead["emails"] == ["a@x.es", "b@x.es"]
    assert lead["rating"] == 4.2 and lead["reviews"] == 10
    assert 0 < lead["distance_km"] < 3


def test_to_lead_rejects_outside_radius_and_missing_data():
    assert to_lead(row(latitude="41.55"), ORIGIN, 10) is None  # ~21 km
    assert to_lead(row(place_id=""), ORIGIN, 10) is None
    assert to_lead(row(latitude=""), ORIGIN, 10) is None
