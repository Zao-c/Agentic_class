from pathlib import Path

import pytest

from scripts.run_profile import PROFILE_FILES, load_profile, parse_env_file


def test_profiles_have_expected_strategies():
    expected = {
        "portable": "hybrid_rerank",
        "agentic-online": "hybrid_rerank",
        "agentic-quality": "neural_hybrid_rerank",
        "neural-online": "neural_hybrid",
        "neural-quality": "neural_hybrid_rerank",
    }
    for profile, strategy in expected.items():
        environment = {}
        values = load_profile(profile, environment)
        assert values["RETRIEVAL_STRATEGY"] == strategy
        assert values["AGENT_PROFILE"] in {
            "portable",
            "agentic-online",
            "agentic-quality",
        }
        assert environment["RETRIEVAL_STRATEGY"] == strategy
        assert PROFILE_FILES[profile].exists()


def test_invalid_profile_line_is_rejected(tmp_path: Path):
    invalid = tmp_path / "invalid.env"
    invalid.write_text("RETRIEVAL_STRATEGY\n", encoding="utf-8")
    with pytest.raises(ValueError, match="KEY=VALUE"):
        parse_env_file(invalid)
