import pytest

from zero_downtime.application.use_cases import HandleRequest
from zero_downtime.domain.profile import RequestProfile
from zero_downtime.interfaces.http import create_app


class Instant:
    def fraction(self):
        return 0.0

    def burn(self, milliseconds):
        return None

    def wait(self, milliseconds):
        return None


@pytest.fixture
def client():
    instant = Instant()
    return create_app(HandleRequest(RequestProfile(), instant, instant, instant)).test_client()


def test_the_root_does_the_work_and_names_the_pod(client):
    body = client.get("/").get_json()
    assert body["status"] == "ok"
    assert body["pod"]
    assert body["latency_ms"] == 50.0


def test_healthz_and_readyz_answer(client):
    assert client.get("/healthz").get_json() == {"status": "alive"}
    assert client.get("/readyz").get_json() == {"status": "ready"}
