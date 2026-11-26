import httpx
import pytest

from odn.config import settings
from odn.platform import github_copilot


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch):
    """Answers GitHub's token endpoint with the queued replies and /user with a login."""
    replies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "octo"})
        assert request.url.path == "/login/oauth/access_token"
        assert b"client_id=Ov23-test" in request.content
        return httpx.Response(200, json=replies.pop(0))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "github_client_id", "Ov23-test")
    monkeypatch.setattr(github_copilot.httpx, "post", client.post)
    monkeypatch.setattr(github_copilot.httpx, "get", client.get)
    return replies


def test_device_flow_poll_maps_github_replies(github: list[dict]) -> None:
    github += [
        {"error": "authorization_pending"},
        {"error": "slow_down", "interval": 10},
        {"error": "access_denied"},
        {"access_token": "gho_secret", "token_type": "bearer"},
    ]
    assert github_copilot.poll_device_flow("dc").status == "pending"
    slow = github_copilot.poll_device_flow("dc")
    assert (slow.status, slow.interval) == ("slow_down", 10)
    assert github_copilot.poll_device_flow("dc").status == "denied"
    done = github_copilot.poll_device_flow("dc")
    assert (done.status, done.token, done.login) == ("connected", "gho_secret", "octo")


def test_sign_in_is_unavailable_without_a_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "github_client_id", "")
    assert not github_copilot.enabled()
    with pytest.raises(github_copilot.GitHubUnavailable):
        github_copilot.start_device_flow()


def test_recommended_model_is_the_strongest_low_cost_one() -> None:
    m = github_copilot.CopilotModel
    models = [
        m(id="auto"),
        m(id="big", price_category="high", input_price=400),
        m(id="mid", price_category="medium", input_price=200),
        m(id="tiny", price_category="low", input_price=10),
        m(id="able", price_category="low", input_price=100),
    ]
    assert github_copilot.recommended_model(models) == "able"
    assert github_copilot.recommended_model([m(id="mid", price_category="medium", input_price=200)]) == "mid"
    assert github_copilot.recommended_model([m(id="auto")]) is None
