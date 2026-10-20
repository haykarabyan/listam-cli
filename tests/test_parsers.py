from pathlib import Path

import pytest

from listam import parsers
from listam.client import AmbiguousName, item_id_from, resolve_option

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text()


def test_search_list_layout_sections_and_cards():
    data = parsers.parse_search(read("search_list.html"))
    assert data["total"] == 2
    assert data["has_next_page"] is True
    sections = [r["section"] for r in data["results"]]
    assert sections[:2] == ["regular", "regular"]
    assert set(sections[2:]) == {"similar"}

    first = data["results"][0]
    assert first["id"] == "23827144"
    assert first["price"] == {"amount": 18000, "currency": "AMD", "text": "18,000 ֏"}
    assert first["subtitle"] == "Suitable For Toyota Camry"
    assert first["location"] == "Erebuni"
    assert first["photo_count"] == 4
    assert first["images"][0] == "https://img.list.am/f/659/98467659.jpg"


def test_search_grid_layout():
    data = parsers.parse_search(read("search_grid.html"))
    assert {r["section"] for r in data["results"]} == {"top", "regular"}
    card = next(r for r in data["results"] if r["section"] == "regular")
    assert card["title"] and card["location"]
    assert card["price"]["currency"] in ("AMD", "USD")
    # grid cards preview fewer photos than the listing has
    assert card["photo_count"] >= len(card["images"])


def test_item_page():
    d = parsers.parse_item(read("item_brake.html"), "22973564")
    assert d["title"].startswith("Forte kashiyama")
    assert d["price"]["amount"] == 12000 and d["price"]["currency"] == "AMD"
    assert [c["currency"] for c in d["price"]["converted"]] == ["USD", "RUB"]
    assert d["category_id"] == 1679
    assert d["attributes"]["Manufacturer"] == "Kashiyama"
    assert d["compatibility"][0] == {"vehicle": "Hyundai Elantra", "generations": ["MD (2010 - 2014)", "AD (2015 - 2020)"]}
    assert d["seller"]["name"] == "Example Brake Shop"
    assert d["images"] == ["https://img.list.am/f/401/92103401.jpg"]
    assert len(d["price_history"]) == 2


def test_item_gallery_has_all_photos():
    d = parsers.parse_item(read("item.html"), "22885953")
    assert len(d["images"]) == 6


def test_phone_popup():
    d = parsers.parse_phone(read("phone.html"), "22973564")
    assert [p["e164"] for p in d["phones"]] == ["+37499000001", "+37499000002", "+37499000003"]
    assert d["phones"][0]["display"] == "(099) 00-00-01"
    assert d["viber"] == ["viber://chat?number=+37499000003"]
    assert d["whatsapp"] == ["https://wa.me/37499000003"]


def test_categories_and_filters():
    cats = parsers.parse_categories(read("category_530.html"))
    names = {c["id"]: c["name"] for c in cats}
    assert names[535] == "Braking Systems"
    f = parsers.parse_filters(read("category_530.html"))
    assert {"n", "cc_bid", "bid", "price1", "price2", "crc", "type"} <= set(f)
    toyota = next(o for o in f["cc_bid"]["options"] if o["name"] == "Toyota")
    assert toyota["value"] == "76"


def test_parse_chain():
    data = {"cc_mid": [[76, "!Toyota"], [1560, " Camry"], [1568, " Corolla"]]}
    out = parsers.parse_chain(data, "cc_mid")
    assert out["parent"] == {"id": 76, "name": "Toyota"}
    assert out["items"][0] == {"id": 1560, "name": "Camry"}


def test_resolve_option():
    opts = [
        {"value": "6779", "name": "XV70 (2017 - 2021)"},
        {"value": "9390", "name": "XV70 Restyling (2020 - 2024)"},
        {"value": "1", "name": "Yerevan", "is_group": True},
    ]
    assert resolve_option(opts, "xv70", "gen") == "6779"
    assert resolve_option(opts, "9390", "gen") == "9390"
    assert resolve_option(opts, "yere", "loc") == "1"
    with pytest.raises(AmbiguousName):
        resolve_option(opts, "xv", "gen")


@pytest.mark.parametrize(
    "value",
    ["22973564", "https://www.list.am/en/item/22973564", "https://www.list.am/item/22973564?ld_src=4"],
)
def test_item_id_from(value):
    assert item_id_from(value) == "22973564"


def test_parse_price_variants():
    assert parsers.parse_price("$ 158,000") == {"amount": 158000, "currency": "USD", "text": "$ 158,000"}
    assert parsers.parse_price("Negotiable")["amount"] is None


def test_item_electronics_attribute_tiles_and_rating():
    d = parsers.parse_item(read("item_laptop.html"), "995163338")
    assert d["attributes"]["Memory (RAM)"] == "16 GB"
    assert d["attributes"]["Condition"] == "Used"
    assert d["attributes"]["Make"] == "Apple"
    assert d["seller"]["rating"] == {"score": 3.7, "reviews": 18}


def test_item_machine_translation_marker_is_split_out():
    d = parsers.parse_item(read("item_laptop.html"), "995163338")
    assert d["description_translation"] == "Translated from Armenian"
    assert "Translated from" not in d["description"]


def test_filters_label_first_tiles_get_own_label_and_options():
    f = parsers.parse_filters(read("filters_116.html"))
    assert f["_a137"]["label"] == "Operating System"
    assert [o["name"] for o in f["_a137"]["options"]][:2] == ["Microsoft Windows", "MacOS"]
    assert f["_a24"]["label"] == "Screen Size"
    assert f["_a138"]["label"] == "Processor"
    assert any(o["name"] == "Apple M5" for o in f["_a138"]["options"])
    assert [o["name"] for o in f["cnd"]["options"]] == ["New", "Used", "Faulty"]
    assert f["price1"]["options"] == []


def test_resolve_option_passes_free_text_through():
    assert resolve_option([], "anything", "q") == "anything"


def test_landing_page_categories_from_shortcuts():
    cats = parsers.parse_categories(read("category_54_landing.html"))
    assert {"id": 62, "name": "For sale", "selected": False} in cats
    assert len(cats) == 5


@pytest.mark.parametrize(
    "fixture, expected",
    [("category_16_landing.html", {"id": 23, "name": "Passenger Cars"}), ("category_29_landing.html", {"id": 1454, "name": "Technology and IT"})],
)
def test_landing_page_categories_from_tiles(fixture, expected):
    cats = parsers.parse_categories(read(fixture))
    assert dict(expected, selected=False) in cats
    assert len({c["id"] for c in cats}) == len(cats)


CAR_LABELS = {"Body Type", "Cleared Customs", "Current Condition", "Drive Type", "Engine Type", "Exchange possible",
              "Gas Equipment", "Make", "Model", "Steering Wheel", "Sunroof", "Transmission", "Year from", "Mileage (km) from"}


def test_item_tiles_oriented_by_known_labels():
    d = parsers.parse_item(read("item_car.html"), "995182429", known_labels=CAR_LABELS)
    a = d["attributes"]
    # value-first tiles
    assert a["Make"] == "Toyota" and a["Year"] == "2020" and a["Mileage"] == "105,000 km"
    assert a["Current Condition"] == "Not damaged" and a["Sunroof"] == "No Sunroof"
    # label-first tiles (same markup)
    assert a["Cleared Customs"] == "Yes" and a["Exchange possible"] == "No"
    assert "Heated seats" in d["features"]
    assert d["seller"]["name"] == "Example Seller"


def test_item_skips_disabled_features():
    # list.am renders every comfort option and greys out the missing ones.
    html = read("item_car.html")
    for name in ("Heated steering wheel", "Cruise control"):
        tile = html.rindex('class="at3"', 0, html.index(f">{name}<"))
        html = html[:tile] + 'class="at3 disabled"' + html[tile + len('class="at3"'):]
    d = parsers.parse_item(html, "995182429", known_labels=CAR_LABELS)
    assert d["features"] == ["Air conditioner", "Heated seats", "Ventilated seats", "Tinted windows"]


@pytest.mark.parametrize(
    "first, second, key",
    [("16 GB", "Memory (RAM)", "Memory (RAM)"), ("Condition", "Used", "Condition"),
     ("Wheelchair accessible", "No", "Wheelchair accessible"), ("Luxe Safety", "Trim", "Trim")],
)
def test_orient_tile(first, second, key):
    assert parsers._orient_tile(first, second, {"condition", "memory"})[0] == key


def test_search_job_cards():
    data = parsers.parse_search(read("search_jobs.html"))
    job = data["results"][0]
    assert job["title"] and job["subtitle"]  # position and employer
    assert job["location"] and job["date"]
    assert job["labels"]  # schedule / experience pills


def test_card_with_several_metadata_rows_picks_district():
    html = (
        '<div id="contentr"><div class="hdr regular-posts">Regular Ads</div>'
        '<a data-tslider="100993164" data-tslider-np="1" href="/en/item/21487715" data-testid="favorite-ad-card-21487715">'
        '<div class="pt">Corner sofa</div><div class="category-data-list-card__meta">'
        '<div class="at category-data-list-card__metadata">Textile · Gray</div>'
        '<div class="c category-data-list-card__metadata">Furniture › Sofas and Armchairs › Sofas</div></div>'
        '<span class="category-data-list-card__amount">205,000<span class="category-data-list-card__currency">֏</span></span>'
        '<div class="category-data-list-card__where-when"><div class="l category-data-list-card__metadata">Kanaker-Zeytun</div></div>'
        "</a></div>"
    )
    card = parsers.parse_search(html)["results"][0]
    assert card["location"] == "Kanaker-Zeytun"
    assert card["subtitle"] == "Textile · Gray"
    assert card["category_path"] == "Furniture › Sofas and Armchairs › Sofas"
    assert card["price"]["amount"] == 205000
