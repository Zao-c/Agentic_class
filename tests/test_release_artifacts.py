import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_container_includes_structured_course_data():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "COPY data/structured ./data/structured" in dockerfile
    assert "COPY data/public_sample ./data/public_sample" in dockerfile
    assert "COPY data/active" not in dockerfile
    assert "ALARM_CODE_DATA_PATH: /app/data/structured/alarm_codes_v1.json" in compose
    assert "KNOWLEDGE_POINT_DATA_PATH: /app/data/structured/knowledge_points_v1.json" in compose
    assert "./data/structured:/app/data/structured:ro" in compose


def test_static_openapi_matches_release_and_core_endpoints():
    schema = json.loads((PROJECT_ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert schema["info"]["version"] == "0.5.0"
    expected_paths = {
        "/api/v1/chat",
        "/api/v1/runs/{run_id}/stream",
        "/api/v1/traces/{request_id}",
        "/api/v1/knowledge/points",
        "/api/v1/exercises/{exercise_id}/submit",
        "/api/v1/evaluations/diagnosis",
        "/api/v1/evaluations/tutoring",
    }
    assert expected_paths <= set(schema["paths"])


def test_public_proof_materials_exist():
    required = [
        "README.md",
        "docs/quickstart.md",
        "docs/demo-guide.md",
        "docs/project-case-study.md",
        "docs/architecture.md",
        "docs/benchmark-baseline.md",
        "docs/typical-bad-case.md",
        "scripts/demo_scenarios.py",
    ]
    for relative in required:
        path = PROJECT_ROOT / relative
        assert path.exists() and path.stat().st_size > 100, relative


def test_web_ui_exposes_accessible_tutoring_and_progress_flows():
    html = (PROJECT_ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    styles = (PROJECT_ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
    assert 'class="skip-link"' in html
    assert 'aria-live="polite"' in html
    assert 'data-view="progress"' in html
    assert 'data-view="teacher"' in html
    assert "/exercises/${exercise.exercise_id}/submit" in script
    assert "/progress`" in script
    assert "/classes/progress-summary" in script
    assert "criterion_results" in script
    assert "prefers-reduced-motion" in styles
    assert "button:focus-visible" in styles
