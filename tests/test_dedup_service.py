import pytest
from app.services import dedup_service


class MockRedis:
    def __init__(self, results=None):
        self.results = results or []
        self.pipelines = []
        self.deleted_keys = []
        self.set_keys = {}

    def pipeline(self):
        pipe = MockPipeline(self)
        self.pipelines.append(pipe)
        return pipe


class MockPipeline:
    def __init__(self, redis):
        self.redis = redis
        self.commands = []

    def set(self, key, val, nx=True, ex=None):
        self.commands.append(("set", key))
        return self

    def delete(self, key):
        self.commands.append(("delete", key))
        return self

    async def execute(self):
        results = []
        for cmd, key in self.commands:
            if cmd == "set":
                val = self.redis.results.pop(0) if self.redis.results else True
                results.append(val)
            elif cmd == "delete":
                self.redis.deleted_keys.append(key)
                results.append(1)
        return results


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Db:
    def __init__(self, results=None):
        self.results = results or []
        self.added = []
        self.flushed = False

    async def execute(self, stmt):
        if self.results:
            return self.results.pop(0)
        return _Result([])

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True


@pytest.mark.anyio
async def test_dedup_service_redis_reserve_success(monkeypatch):
    r = MockRedis([True, False])
    monkeypatch.setattr(dedup_service, "_get_redis", lambda: r)

    res = await dedup_service._reserve_via_redis(client_id=1, candidate_ids=["id1", "id2"])
    assert res == {"id1"}  # first one True, second False
    assert len(r.pipelines) == 1
    assert r.pipelines[0].commands == [("set", "dedup:1:id1"), ("set", "dedup:1:id2")]


@pytest.mark.anyio
async def test_dedup_service_redis_rollback(monkeypatch):
    r = MockRedis()
    monkeypatch.setattr(dedup_service, "_get_redis", lambda: r)

    await dedup_service.rollback_redis_dedup(client_id=1, candidate_ids=["id1", "id2"])
    assert r.deleted_keys == ["dedup:1:id1", "dedup:1:id2"]


@pytest.mark.anyio
async def test_dedup_service_reserve_unique_sqlite(monkeypatch):
    r = MockRedis([True, True])
    monkeypatch.setattr(dedup_service, "_get_redis", lambda: r)

    # Mock engine dialect as sqlite
    monkeypatch.setattr(dedup_service.engine.dialect, "name", "sqlite")

    db = _Db([_Result(["id2"])])  # id2 already exists in DB

    res = await dedup_service.reserve_unique_event_ids(db, client_id=1, candidate_ids=["id1", "id2"])
    assert res == {"id1"}  # only id1 is reserved because id2 exists in DB
    assert len(db.added) == 1
    assert db.added[0].event_id == "id1"
    assert db.flushed is True
    # Verify that the conflicted id2 was rolled back in Redis
    assert r.deleted_keys == ["dedup:1:id2"]
