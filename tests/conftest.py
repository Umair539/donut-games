import pytest
from fastapi.testclient import TestClient

from Server.core import app as server


@pytest.fixture
def client():
    server.rooms.rooms.clear()
    with TestClient(server.app) as c:
        yield c
