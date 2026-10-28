"""Keep the original O2 150k failure honest; verify family filters at 170k."""

from datetime import date

from fastapi.testclient import TestClient

from app.main import create_app
from app.u2_eval import forbid_network, offline_settings, parse_sse

ORIGINAL = "В Анталью 15.11.2026 на 7 ночей, двое и дочка 5 лет, бюджет 150к"


def family_query(message):
    app = create_app(offline_settings())
    with forbid_network(), TestClient(app) as client:
        app.state.agent._today = lambda: date(2026, 10, 28)
        events = parse_sse(client.post("/api/chat", json={"message": message}).text)
        question = next(event["data"] for event in events if event["type"] == "question")
        assert question["field"] == "origin"
        session_id = events[-1]["data"]["session_id"]
        response = client.post("/api/chat", json={"message": "Из Москвы", "session_id": session_id})
        assert response.status_code == 200
        events = parse_sse(response.text)
        assert events[-1]["type"] == "done" and not any(event["type"] == "error" for event in events)
        state = client.get(f"/api/session/{session_id}").json()
    rows = next(event["data"]["items"] for event in events if event["type"] == "options")
    answer = "".join(event["data"]["text"] for event in events if event["type"] == "delta")
    assert state["brief"]["adults"] == 2 and state["brief"]["children_ages"] == [5]
    assert state["brief"]["start_date"] == "2026-11-15" and state["brief"]["nights"] == 7
    assert not any(event["type"] == "booking" for event in events)
    return rows, answer, state


def test_original_150k_budget_returns_no_fabricated_affordable_family_package():
    rows, answer, state = family_query(ORIGINAL)
    assert rows == [] and state["brief"]["budget_rub"] == 150000
    assert "превышают бюджет" in answer and "не подтверждён" in answer


def test_170k_positive_control_returns_nonempty_family_safe_packages_within_group_budget():
    rows, answer, state = family_query(ORIGINAL.replace("150к", "170к"))
    assert rows and state["brief"]["budget_rub"] == 170000
    for package in rows:
        assert package["hotel"]["adults_only"] is False
        assert 0 < package["total_rub"] <= 170000
        assert package["party_price_scope"] == "whole_party"
        assert package["travelers"] == {"adults": 2, "children_ages": [5], "pets": 0}
        assert package["is_synthetic"] is True
    assert "демонстрационные" in answer
