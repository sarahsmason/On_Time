import pytest

from on_time import server
from on_time.storage import SqliteRepository


@pytest.fixture(autouse=True)
def fresh_home():
    """Every test gets its own in-memory database with the default lights."""
    repo = SqliteRepository()
    home = server.configure(repo)
    yield home
    repo.close()
