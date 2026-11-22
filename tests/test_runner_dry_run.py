import sys

import src.run_experiment as runner
from src import config


def test_dry_run_succeeds_without_groq_or_provider_client(monkeypatch, capsys):
    client_constructions = []
    loaded_paths = []

    monkeypatch.setitem(sys.modules, "groq", None)
    monkeypatch.setattr(
        runner.ResultsStore,
        "load",
        staticmethod(
            lambda path, meta: (
                loaded_paths.append(path)
                or runner.ResultsStore(path, meta)
            )
        ),
    )
    monkeypatch.setattr(
        runner,
        "create_groq_client",
        lambda **kwargs: client_constructions.append(kwargs),
    )

    assert runner.main([
        "--split", "dev",
        "--prompts", "A", "B-k3", "B-k5", "B-k8", "C",
        "--runs", "1",
        "--dry-run",
    ]) == 0

    output = capsys.readouterr().out
    assert "Total planned calls: 100" in output
    assert "Calls to do: 100" in output
    assert "Dry run; no API call was made." in output
    assert client_constructions == []
    assert loaded_paths == [config.DEVELOPMENT_RESULTS_PATH]


def test_runner_defaults_to_plan_specific_outputs():
    legacy_results = config.OUTPUTS_DIR / "raw_responses.json"
    legacy_retry_log = config.OUTPUTS_DIR / "retry_log.jsonl"

    assert runner.RESULTS_PATH == config.DEVELOPMENT_RESULTS_PATH
    assert runner.RETRY_LOG_PATH == config.DEVELOPMENT_RETRY_LOG_PATH
    assert runner.RESULTS_PATH != legacy_results
    assert runner.RETRY_LOG_PATH != legacy_retry_log
