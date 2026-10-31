"""§B4.3 startup: load the graph when Aura is empty, degrade (don't crash) when it is unreachable."""
import ingestion.load_graph
from app.main import ensure_graph


class FakeGraph:
    def __init__(self, reachable: bool, nodes: int):
        self.reachable, self.nodes = reachable, nodes

    async def connect(self):
        return self.reachable

    async def node_count(self):
        return self.nodes


async def test_empty_graph_is_loaded(monkeypatch):
    calls = []
    monkeypatch.setattr(ingestion.load_graph, "load_all", lambda: calls.append(1) or (136, 542))
    assert await ensure_graph(FakeGraph(True, 0)) is True
    assert calls == [1]


async def test_populated_graph_is_left_alone(monkeypatch):
    monkeypatch.setattr(ingestion.load_graph, "load_all", lambda: (_ for _ in ()).throw(AssertionError))
    assert await ensure_graph(FakeGraph(True, 136)) is True


async def test_unreachable_graph_degrades():
    assert await ensure_graph(FakeGraph(False, 0)) is False
