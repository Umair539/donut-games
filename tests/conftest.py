import pytest
from fastapi.testclient import TestClient

from Server.core import app as server
from Server.core.limits import AddressLimits


@pytest.fixture
def client():
    server.rooms.rooms.clear()
    server.limits = AddressLimits()
    with TestClient(server.app) as c:
        yield c
