import re
from pathlib import Path
import sys

from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backend.app_session import SESSION_HEADER, install_app_session


def make_app(tmp_path, research=False):
    index = tmp_path / "index.html"
    index.write_text("<html><head><title>Fixture</title></head><body></body></html>", encoding="utf-8")
    app = Flask(__name__)
    app.testing = True
    app.config["PAPER_RUNTIME_IDENTITY"] = {
        "backend": "research_inspection_python" if research else "paper_python",
        "optimizerSha256": "a" * 64,
        "profileContractSha256": "b" * 64,
        "researchDisplayManifestSha256": "c" * 64 if research else None,
        "notPublic": "/local/private/file",
    }
    mutation_calls = []

    @app.get("/api/status")
    def status():
        return jsonify(ok=True, atlasLoaded=True)

    install_app_session(app, index, tmp_path / "replays")

    @app.before_request
    def mutate():
        if request.path == "/api/fixture" and request.method == "POST":
            mutation_calls.append(True)
            return jsonify(ok=True)
        return None

    return app.test_client(), mutation_calls


def test_app_session_is_bound_in_html_and_status_without_local_paths(tmp_path):
    client, _ = make_app(tmp_path, research=True)
    index = client.get("/")
    session = re.search(rb'name="probeplanner-session" content="([0-9a-f]{32})"', index.data)[1].decode()
    assert index.headers["Cache-Control"] == "no-store"
    assert client.get("/index.html").data == index.data
    assert client.head("/").data == b""
    resp = client.get("/api/status", headers={SESSION_HEADER: session})
    assert resp.headers[SESSION_HEADER] == session
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.get_json()["appSession"] == {
        "id": session, "mode": "research", "optimizerSha256": "a" * 64,
        "profileSha256": "b" * 64, "displaySha256": "c" * 64,
    }


def test_stale_session_cannot_call_mutating_route_after_restart(tmp_path):
    old, _ = make_app(tmp_path)
    old_id = old.get("/api/status").headers[SESSION_HEADER]
    new, calls = make_app(tmp_path)
    new_status = new.get("/api/status")
    new_id = new_status.headers[SESSION_HEADER]
    assert new_status.get_json()["appSession"]["mode"] == "planning"
    assert old_id != new_id
    error = new.post("/api/fixture", headers={SESSION_HEADER: old_id})
    assert error.status_code == 409 and error.get_json()["code"] == "app_session_changed"
    assert not calls
    assert new.post("/api/fixture", headers={SESSION_HEADER: new_id}).status_code == 200
    assert new.get("/api/app/session", headers={SESSION_HEADER: new_id}).get_json() == {"ok": True}
    assert len(calls) == 1


def test_replay_download_retains_exact_saved_bytes_and_is_not_available_for_research(tmp_path):
    folder = tmp_path / "replays"
    folder.mkdir()
    raw = b'{ "requestId":"run42", "response": { "stability": -0.0 } }\n'
    (folder / "run42.json").write_bytes(raw)
    client, _ = make_app(tmp_path)
    resp = client.get("/api/replay/run42/download")
    assert resp.status_code == 200 and resp.data == raw
    assert resp.headers["Cache-Control"] == "no-store"
    assert client.get("/api/replay/missing/download").status_code == 404
    research, _ = make_app(tmp_path, research=True)
    assert research.get("/api/replay/run42/download").status_code == 409
