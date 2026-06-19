from usage_widget import copilot


def test_num_coercion():
    assert copilot._num("3") == 3.0
    assert copilot._num(2) == 2.0
    assert copilot._num(None) == 0.0
    assert copilot._num("nope") == 0.0


def test_extract_line_items_and_limit():
    data = {
        "usageItems": [
            {"totalQuantity": 10},
            {"totalQuantity": 5},
        ],
        "included": 300,
    }
    used, limit = copilot._extract(data)
    assert used == 15.0
    assert limit == 300.0


def test_extract_snake_case_and_single_total():
    used, limit = copilot._extract({"usage_items": [{"quantity": 7}]})
    assert used == 7.0
    assert limit is None
    used2, _ = copilot._extract({"total_quantity_used": 42})
    assert used2 == 42.0


def test_extract_empty():
    assert copilot._extract({}) == (0.0, None)
    assert copilot._extract("garbage") == (0.0, None)


def test_no_token_returns_error(monkeypatch):
    monkeypatch.setattr(copilot.config, "GITHUB_OAUTH_TOKEN", "")
    monkeypatch.setattr(copilot.config, "GITHUB_USERNAME", "")
    assert copilot.get_copilot_usage() == {"ok": False, "error": "no token"}
