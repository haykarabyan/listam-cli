import re
from pathlib import Path

import pytest

from listam import posting
from listam.http import ListamError

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text()


@pytest.fixture
def info():
    data = posting.parse_ad_form(read("post_form_1679.html"))
    data["category"] = 1679
    data["defaults"] = {"location": "3", "currency": "0"}
    return data


def make_poster():
    # build() only needs the API for resolving "fits"; skip login for offline tests.
    return object.__new__(posting.Poster)


def test_parse_ad_form(info):
    f = info["fields"]
    assert f["condition"] == {"label": "Condition", "required": True, "kind": "choice",
                              "options": [{"value": "1", "name": "New"}, {"value": "2", "name": "Used"}]}
    assert f["manufacturer"]["required"] and any(o["value"] == "99999" for o in f["manufacturer"]["options"])
    assert f["title"]["kind"] == "text" and f["price"]["options"] == []
    assert "submission_token" not in f and "for_which_cars_" not in f
    assert info["has_compatibility"] and info["has_part_number"]
    assert (info["min_photos"], info["max_photos"]) == (1, 12)
    assert [p["id"] for p in info["phones"]] == ["100001", "0"]
    assert info["dynamic_fields"] == []


def test_build_resolves_names_and_hides_internal_fields(info):
    spec = {
        "category": 1679, "title": "Brake pads", "description": "Front pads", "price": 15000,
        "currency": "amd", "photos": ["a.jpg"], "phone": False, "part_number": "04465-33450",
        "fields": {"condition": "new", "manufacturer": "other", "car_part_side": "not applicable",
                   "Car Part Position": "front"},
    }
    values, summary = make_poster().build(spec, info)
    assert values["condition"] == "1" and values["manufacturer"] == "99999"
    assert values["car_part_position"] == "1" and values["currency"] == "0"
    assert values["contact_information_phone"] == "0"
    assert values["auto_part_number[0]"] == "04465-33450"
    assert summary["Price"] == "15000 ֏"
    assert summary["Location"].endswith("(account default)")


def test_build_reports_missing_and_bad_values(info):
    with pytest.raises(ListamError, match="missing required fields: .*Manufacturer"):
        make_poster().build({"category": 1679, "title": "x", "description": "y", "price": 1,
                             "photos": ["a.jpg"], "fields": {"condition": "new"}}, info)
    with pytest.raises(ListamError, match="post-form 1679 --name condition"):
        make_poster().build({"category": 1679, "fields": {"condition": "brand new"}}, info)
    with pytest.raises(ListamError, match="at least 1 photo"):
        make_poster().build({"category": 1679, "title": "x", "description": "y", "price": 1,
                             "fields": {"condition": "new", "manufacturer": "other",
                                        "car_part_side": "left", "car_part_position": "front"}}, info)


def test_submit_never_selects_paid_promotion():
    sent = {}

    class FakeHttp:
        def post(self, url, data=None, headers=None):
            sent["data"] = data

    poster = make_poster()
    poster.http = FakeHttp()
    base = [("selected_premium", "7"), ("payment_method", "card"), ("selected_bundle_id", "3"), ("title", "x")]
    poster._submit("/en/add/1679/0", base, {}, ["h1", "h2"], "Post")
    data = dict(sent["data"])
    assert data["selected_premium"] == "" and data["payment_method"] == "" and data["selected_bundle_id"] == "-1"
    assert data["_form_action"] == "Post" and data["_form_action1"] == "Post"
    assert [v for k, v in sent["data"] if k == "ufiles[]"] == ["h1", "h2"]
    assert data["ufilessort"] == "h1,h2"


def test_parse_my_ads_and_delete_urls():
    html = (
        '<div class="my1" data-post-id="3858824" data-role="my-ad-card">'
        '<span data-testid="my-ad-card-status" data-state="drafts"></span>'
        '<a href="/add/1679/0/0?dp=3858824&amp;pc=abc" data-testid="my-ad-card-post-link">'
        '<span data-testid="my-ad-card-title">Brake pads</span></a>'
        '<div data-testid="my-ad-card-price">15,000 ֏</div><div class="category-breadcrumbs">Braking Systems</div>'
        "<a onclick=\"deldraft('3858824')\">Delete</a></div>"
        '<div class="my1" data-post-id="24000001" data-role="my-ad-card">'
        '<span data-testid="my-ad-card-status" data-state="active"></span>'
        '<span data-testid="my-ad-card-title">Old pads</span>'
        "<a onclick=\"delpost(24000001, 1)\">Delete</a></div>"
    )
    draft, active = posting.parse_my_ads(html)
    assert draft["id"] == "3858824" and draft["status"] == "drafts" and draft["title"] == "Brake pads"
    assert draft["price"]["amount"] == 15000
    assert draft["_delete"] == "/ad-delete?i=3858824&m=2"
    assert active["_delete"] == "/ad-delete?i=24000001&m=1"


def test_current_step_and_form_errors():
    html = read("post_form_1679.html")
    assert posting.current_step(html) == "Compose"
    page = re.sub(r'(<input[^>]*name="title")', r'<div class="err">This field cannot be empty.</div>\1', html, count=1)
    errors = posting.parse_form_errors(page)
    assert errors and errors[0]["message"] == "This field cannot be empty."
    assert errors[0]["field"] in ("title", "Title")


def test_load_spec_resolves_photo_paths(tmp_path):
    spec_file = tmp_path / "ad.json"
    spec_file.write_text('{"category": 1679, "photos": ["front.jpg", "/abs/back.jpg"]}')
    spec = posting.load_spec(str(spec_file))
    assert spec["photos"] == [str(tmp_path / "front.jpg"), "/abs/back.jpg"]
    with pytest.raises(ListamError, match="cannot read ad spec"):
        posting.load_spec(str(tmp_path / "missing.json"))
