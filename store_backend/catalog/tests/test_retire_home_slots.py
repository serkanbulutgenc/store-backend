"""The home Banner Slots become section images: the stored layouts follow (#172)."""

from __future__ import annotations

import importlib

migration = importlib.import_module(
    "store_backend.catalog.migrations.0024_retire_home_banner_slots",
)
upgrade = migration.upgrade_sections


def test_chosen_banners_become_own_slides_with_the_same_ids():
    old = [
        {
            "id": "s",
            "type": "slider",
            "settings": {"source": "banners", "banner_ids": [7, 3]},
        },
    ]

    [section] = upgrade(old, hero_ids=[1], mid_ids=[])

    assert section["settings"] == {"source": "own", "banner_ids": [7, 3]}


def test_the_home_source_takes_the_live_hero_banners_when_there_are_some():
    old = [{"id": "hero", "type": "slider"}]

    [section] = upgrade(old, hero_ids=[4, 5, 6], mid_ids=[])

    assert section["settings"] == {"source": "own", "banner_ids": [4, 5, 6]}


def test_the_home_source_keeps_its_product_fallback_without_hero_banners():
    old = [{"id": "hero", "type": "slider", "settings": {"count": 3}}]

    [section] = upgrade(old, hero_ids=[], mid_ids=[9])

    assert section == {"id": "hero", "type": "slider", "settings": {"count": 3}}


def test_the_hero_banners_are_cut_to_the_sliders_count():
    old = [{"id": "hero", "type": "slider", "settings": {"count": 2}}]

    [section] = upgrade(old, hero_ids=[1, 2, 3], mid_ids=[])

    assert section["settings"]["banner_ids"] == [1, 2]


def test_a_strip_that_followed_a_position_now_names_its_banner():
    old = [
        {"id": "mid-1", "type": "banner", "settings": {"index": 0}},
        {"id": "mid-2", "type": "banner", "settings": {"index": 1}},
    ]

    first, second = upgrade(old, hero_ids=[], mid_ids=[11, 12])

    assert first["settings"] == {"banner_id": 11}
    assert second["settings"] == {"banner_id": 12}


def test_a_strip_that_named_its_banner_keeps_it_and_loses_the_old_keys():
    old = [
        {"id": "b", "type": "banner", "settings": {"slot": "home_mid", "banner_id": 5}},
    ]

    [section] = upgrade(old, hero_ids=[], mid_ids=[11])

    assert section["settings"] == {"banner_id": 5}


def test_a_strip_with_no_banner_left_is_dropped():
    old = [
        {"id": "mid-1", "type": "banner", "settings": {"index": 3}},
        {"id": "keep", "type": "categories"},
    ]

    result = upgrade(old, hero_ids=[], mid_ids=[11])

    assert [s["id"] for s in result] == ["keep"]


def test_other_sections_are_left_as_they_are():
    old = [{"id": "latest", "type": "products", "settings": {"source": "latest"}}]

    assert upgrade(old, hero_ids=[1], mid_ids=[2]) == old
