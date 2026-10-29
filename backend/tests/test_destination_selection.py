"""Every D5 card and declared alias must survive an explicit choice verbatim."""

import copy
from pathlib import Path

import pytest

from app.agent.destination_selection import resolve_catalogue_destination
from app.tools.destinations import load_catalogue

DATA = Path(__file__).resolve().parents[2] / "data/destinations.yaml"
CATALOGUE = load_catalogue(DATA)
LABELS = [(row.iata, value) for row in CATALOGUE.destinations for value in (row.name, row.iata, *row.aliases)]


@pytest.mark.parametrize("code,label", LABELS)
def test_all_48_names_iata_and_declared_aliases_resolve_for_explicit_choice_and_bare_label(code, label):
    assert resolve_catalogue_destination("Хочу в " + label, catalogue=CATALOGUE) == code
    assert resolve_catalogue_destination(label, catalogue=CATALOGUE) == code


@pytest.mark.parametrize(
    "text,code",
    [
        ("в Шри-Ланку", "CMB"),
        ("на Шри-Ланке", "CMB"),
        ("Хочу в Шри‑Ланка", "CMB"),
        ("Хочу в Шри–Ланку", "CMB"),
        ("Хочу в Шри—Ланку", "CMB"),
        ("Хочу в Шри — Ланку", "CMB"),
        ("Из Москвы в Минеральные Воды 18 ноября 2026 на неделю", "MRV"),
        ("Хочу из Москвы в Шри-Ланку, 2 взрослых", "CMB"),
        ("Хочу в Sri Lanka", "CMB"),
        ("Хочу в Abu Dhabi", "AUH"),
        ("Хочу в Nha Trang", "CXR"),
        ("Хочу в Malé", "MLE"),
        ("Хочу в Male\u0301", "MLE"),
        ("Хочу в Mahé", "SEZ"),
        ("TSE", "NQZ"),
        ("FRU", "BSZ"),
        ("Смени направление на Бали", "DPS"),
        ("Расскажи о направлении CMB", "CMB"),
        ("Расскажи о направлении CMB?", "CMB"),
        ("Теперь хочу в Шарм эль Шейх, на 7 ночей", "SSH"),
    ],
)
def test_hyphens_multiword_unicode_and_historical_aliases_keep_the_catalogue_anchor(text, code):
    assert resolve_catalogue_destination(text, catalogue=CATALOGUE) == code


@pytest.mark.parametrize(
    "text",
    [
        "Почему Шри-Ланка?",
        "Что если в Шри-Ланку",
        "А если хочу в Шри-Ланку",
        "Хочу в Шри-Ланку?",
        "Расскажи об отелях в Шри-Ланке",
        "Сколько стоит в Бангкоке",
        "Хочу узнать про Бали",
        "Хочу в тихий отель",
        "Хочу в номер с видом на море",
        "Хочу на завтрак",
        "Хочу в начале ноября",
        "Хочу в пределах 150 тысяч",
        "Хочу в отель Бали",
        "Хочу в Bali Style Hotel",
        "Хочу в Малеcity",
        "Хочу в Бангкок-резиденцию",
        "Хочу в Шри-Ланку или на Мальдивы",
        "Хочу в Шри-Ланку, в Дубай",
        "Хочу в CMB, DXB",
        "Хочу в Шри-Ланку, лучше не в Дубай",
        "Хочу в Шри-Ланку, точнее не в Дубай",
        "Хочу в Шри-Ланку, но не в Дубай",
        "Хочу в Шри-Ланку, точнее в Таиланд",
        "Не хочу в Шри-Ланку",
        "Хочу не в Бангкок",
        "Хочу в Шри-Ланку не ехать",
        "В Бангкоке красиво",
        "Бали лучше Антальи",
        "ZZZ",
        "Расскажи о направлении ZZZ",
        "Хочу в Минеральные Водички",
        "Хочу в Маленький отель",
        "Турция",
        "Египет",
        "Таиланд",
        "Хочу в Таиланд",
        "",
    ],
)
def test_questions_negations_hotels_countries_and_fragments_do_not_change_the_destination(text):
    assert resolve_catalogue_destination(text, catalogue=CATALOGUE) is None


@pytest.mark.parametrize(
    "text,code",
    [
        ("Хочу в Шри-Ланку, точнее в Дубай", "DXB"),
        ("Хочу в Шри-Ланку, но лучше в Дубай", "DXB"),
        ("Хочу в Шри-Ланку, вернее на Бали", "DPS"),
        ("Хочу в Шри-Ланку, точнее в Дубай, 2 взрослых на 7 ночей", "DXB"),
        ("Хочу в Шри-Ланку, точнее в Дубай, но лучше на Бали", "DPS"),
        ("в Шри-Ланку, точнее в Дубай", "DXB"),
    ],
)
def test_only_an_explicit_correction_can_replace_the_first_catalogue_target(text, code):
    assert resolve_catalogue_destination(text, catalogue=CATALOGUE) == code


def test_longest_whole_label_and_ambiguity_are_independent_of_catalogue_order():
    raw = copy.deepcopy(CATALOGUE.model_dump(mode="json"))
    raw["destinations"][0]["aliases"] += ["Новый", "общий курорт"]
    raw["destinations"][1]["aliases"] += ["Новый город", "общий курорт"]
    assert resolve_catalogue_destination("Хочу в Новый город", catalogue=raw) == raw["destinations"][1]["iata"]
    assert resolve_catalogue_destination("Хочу в общий курорт", catalogue=raw) is None
    raw["destinations"].reverse()
    assert resolve_catalogue_destination("Хочу в общий курорт", catalogue=raw) is None


def test_yo_and_hyphen_normalization_does_not_select_a_prefix_or_mutate_catalogue():
    raw = copy.deepcopy(CATALOGUE.model_dump(mode="json"))
    raw["destinations"][0]["aliases"] += ["Орёл-на-Море"]
    before = copy.deepcopy(raw)
    assert resolve_catalogue_destination("Хочу в Орел‑на‑Море", catalogue=raw) == raw["destinations"][0]["iata"]
    assert resolve_catalogue_destination("Хочу в Орел‑на‑Морец", catalogue=raw) is None
    assert raw == before


def test_injected_data_path_and_configuration_errors_are_not_silently_ignored(tmp_path):
    assert resolve_catalogue_destination("Хочу в CMB", data_path=DATA) == "CMB"
    with pytest.raises(ValueError, match="either"):
        resolve_catalogue_destination("CMB", catalogue=CATALOGUE, data_path=DATA)
    with pytest.raises(OSError):
        resolve_catalogue_destination("CMB", data_path=tmp_path / "missing.yaml")
