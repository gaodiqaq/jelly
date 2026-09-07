from agent_shell.config import Settings
from agent_shell.llm.client import LLMClient
from agent_shell.runtime import ProviderStore
from tests.test_server import make_client


def test_recipe_roundtrip_and_validation(tmp_path):
    with make_client(Settings(cwd=tmp_path, session_dir=tmp_path / "sessions"), []) as client:
        body = {"title": "周报", "instruction": "阅读材料，按项目列出进展。"}
        saved = client.post("/api/recipes", json=body).json()
        assert saved["version"] == 1
        updated = client.put(f"/api/recipes/{saved['id']}", json={**body, "title": "项目周报"})
        assert updated.json()["version"] == 2
        assert client.get("/api/recipes").json()["recipes"][0]["title"] == "项目周报"
        assert client.post("/api/recipes", json={**body, "title": " "}).status_code == 400
        assert client.delete(f"/api/recipes/{saved['id']}").status_code == 200
        assert client.get("/api/recipes").json()["recipes"] == []


def test_studio_routes_require_auth(tmp_path):
    with make_client(
        Settings(cwd=tmp_path, session_dir=tmp_path / "sessions"), [], api_token="test-secret"
    ) as client:
        for route in ("/api/recipes", "/api/sessions/example/run", "/api/sessions/example/changes"):
            assert client.get(route).status_code == 401


def test_model_snapshot_is_stable(tmp_path):
    settings = Settings(model="openai/first")
    store = ProviderStore(tmp_path / "config.yaml")
    store.seed_from_settings(settings)
    store.upsert_provider("openai", api_key="first-key", api_base="http://localhost:1234")
    client = LLMClient(settings, store)
    frozen = client.snapshot()
    store.set_model("openai/second")
    store.upsert_provider("openai", api_key="second-key")
    assert frozen.model == "openai/first"
    assert frozen._resolve()[1] == "first-key"
    assert client.model == "openai/second"
