"""Regression tests for managed local-patches update flow."""

from pathlib import Path


def _init_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)


def test_check_repo_uses_origin_default_when_local_patches_exists(tmp_path, monkeypatch):
    import api.updates as upd

    _init_git_dir(tmp_path)
    (tmp_path / ".git" / "refs" / "heads" / "local-patches").write_text("deadbeef\n", encoding="utf-8")

    calls = []

    def fake_run(args, cwd, timeout=10):
        calls.append(args)
        if args[:3] == ["fetch", "origin", "--quiet"]:
            return "", True
        if args[:2] == ["rev-list", "--count"]:
            assert args[2] == "HEAD..origin/master"
            return "3", True
        if args == ["rev-parse", "--short", "HEAD"]:
            return "aaaa111", True
        if args == ["rev-parse", "--short", "origin/master"]:
            return "bbbb222", True
        return "", True

    monkeypatch.setattr(upd, "_run_git", fake_run)
    monkeypatch.setattr(upd, "_detect_default_branch", lambda _path: "master")

    result = upd._check_repo(tmp_path, "webui")
    assert result["behind"] == 3
    assert result["branch"] == "origin/master"
    assert any(c[:2] == ["rev-list", "--count"] for c in calls)


def test_apply_update_rebases_local_patches_and_switches(tmp_path, monkeypatch):
    import api.updates as upd

    _init_git_dir(tmp_path)
    (tmp_path / ".git" / "refs" / "heads" / "local-patches").write_text("deadbeef\n", encoding="utf-8")

    calls = []

    def fake_run(args, cwd, timeout=10):
        calls.append(args)
        if args[:3] == ["fetch", "origin", "--quiet"]:
            return "", True
        if args[:2] == ["status", "--porcelain"]:
            return "", True
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "local-patches", True
        if args == ["checkout", "master"]:
            return "", True
        if args == ["pull", "--ff-only", "origin", "master"]:
            return "Already up to date.", True
        if args == ["rebase", "--onto", "master", "master", "local-patches"]:
            return "", True
        if args == ["checkout", "local-patches"]:
            return "", True
        return "", True

    monkeypatch.setattr(upd, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(upd, "_run_git", fake_run)
    monkeypatch.setattr(upd, "_detect_default_branch", lambda _path: "master")
    monkeypatch.setattr(upd, "_schedule_restart", lambda delay=2.0: None)

    result = upd._apply_update_inner("webui")
    assert result["ok"] is True
    assert "rebased local-patches onto master" in result["message"]
    assert ["checkout", "master"] in calls
    assert ["pull", "--ff-only", "origin", "master"] in calls
    assert ["rebase", "--onto", "master", "master", "local-patches"] in calls
    assert ["checkout", "local-patches"] in calls
