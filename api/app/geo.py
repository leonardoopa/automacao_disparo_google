import math

import httpx

EARTH_KM = 6371.0088
KM_PER_DEG_LAT = 111.32


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def grid_cells(lat, lon, radius_km, n):
    """Divide o quadrado em volta da origem em n x n células de busca.

    O Maps devolve ~120 resultados por busca, então uma busca só não cobre 10 km.
    Células cujo canto mais próximo já fica fora do círculo são descartadas.
    Retorna [{lat, lon, radius, zoom}] com lat/lon em string, como a API do motor exige.
    """
    km_lon = KM_PER_DEG_LAT * math.cos(math.radians(lat))
    side = 2 * radius_km / n
    half_diag = side * math.sqrt(2) / 2
    zoom = round(math.log2(156543.03 * math.cos(math.radians(lat)) * 1000 / (side * 1000)))
    cells = []
    for i in range(n):
        for j in range(n):
            dy = -radius_km + side * (i + 0.5)
            dx = -radius_km + side * (j + 0.5)
            if math.hypot(dx, dy) - half_diag > radius_km:
                continue
            cells.append(
                {
                    "lat": f"{lat + dy / KM_PER_DEG_LAT:.6f}",
                    "lon": f"{lon + dx / km_lon:.6f}",
                    "radius": max(1000, math.ceil(1.15 * half_diag * 1000)),
                    "zoom": min(17, max(11, zoom)),
                }
            )
    return cells


def geocode(address):
    r = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1},
        headers={"User-Agent": "mercados-led/0.1"},
        timeout=15,
    )
    r.raise_for_status()
    hits = r.json()
    if not hits:
        raise ValueError(f"endereço não encontrado: {address}")
    return float(hits[0]["lat"]), float(hits[0]["lon"])
