"""Bind the served interface to the launcher instance that produced it."""

import re
from uuid import uuid4

from flask import jsonify, request, send_file


SESSION_HEADER = "X-ProbePlanner-Session"


def install_app_session(app, index_file, replay_dir):
    session_id = uuid4().hex
    index = index_file.read_text(encoding="utf-8")
    if index.count("</head>") != 1:
        raise ValueError("Invalid frontend index; rebuild apps/ui")
    meta = f'<meta name="probeplanner-session" content="{session_id}">'
    page = index.replace("</head>", meta + "\n</head>", 1)
    replay_root = replay_dir.resolve()

    @app.get("/api/replay/<request_id>/download")
    def download_app_replay(request_id):
        if app.config["PAPER_RUNTIME_IDENTITY"]["backend"] != "paper_python":
            return jsonify(ok=False, code="app_session_read_only", error="Inspection only."), 409
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", request_id):
            return jsonify(ok=False, code="not_found", error="Saved replay record not found."), 404
        record = (replay_root / f"{request_id}.json").resolve()
        if record.parent != replay_root or not record.is_file():
            return jsonify(ok=False, code="not_found", error="Saved replay record not found."), 404
        response = send_file(record, mimetype="application/json", conditional=False,
                             as_attachment=True, download_name=f"probeplanner_replay_{request_id}.json")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def bind_interface():
        if request.path in ("/", "/index.html") and request.method in ("GET", "HEAD"):
            return app.response_class(page, mimetype="text/html", headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            })
        if request.path.startswith("/api/"):
            expected = request.headers.get(SESSION_HEADER)
            if expected is not None and expected != session_id:
                return jsonify(ok=False, code="app_session_changed",
                               error="The application session changed. Reload this page."), 409
            if request.path == "/api/app/session" and request.method == "GET":
                return jsonify(ok=True)
        return None

    @app.after_request
    def report_session(response):
        if not request.path.startswith("/api/"):
            return response
        response.headers[SESSION_HEADER] = session_id
        if request.path == "/api/app/session":
            response.headers["Cache-Control"] = "no-store"
        if request.path == "/api/status" and request.method == "GET" and response.status_code == 200:
            runtime = app.config["PAPER_RUNTIME_IDENTITY"]
            data = response.get_json()
            data["appSession"] = {
                "id": session_id,
                "mode": "research" if runtime.get("researchDisplayManifestSha256") else "planning",
                "optimizerSha256": runtime["optimizerSha256"],
                "profileSha256": runtime["profileContractSha256"],
                "displaySha256": runtime.get("researchDisplayManifestSha256"),
            }
            response.set_data(app.json.dumps(data))
            response.headers["Cache-Control"] = "no-store"
        return response
