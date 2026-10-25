"""The Live Final persists preparation briefs so a restarted process does not prepare again."""

from hackit2 import cli
from hackit2.prep import briefing


def test_final_enables_a_brief_cache_folder_per_arena_host(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(briefing.BRIEF_CACHE_ENV, "")  # recorded, so the variable is restored afterwards
    monkeypatch.delenv(briefing.BRIEF_CACHE_ENV)
    monkeypatch.setenv("ARENA_URL", "https://arena.tacto.ai")
    folder = cli.enable_final_brief_cache()
    assert folder.startswith("logs/briefs/final-arena.tacto.ai-")
    assert cli.os.environ[briefing.BRIEF_CACHE_ENV] == folder

    monkeypatch.delenv(briefing.BRIEF_CACHE_ENV)
    monkeypatch.setenv("ARENA_URL", "http://127.0.0.1:8766")
    assert "final-127.0.0.1_8766-" in cli.enable_final_brief_cache()  # a rehearsal never shares the folder


def test_an_explicit_brief_cache_is_kept(monkeypatch):
    monkeypatch.setenv(briefing.BRIEF_CACHE_ENV, "/tmp/my-briefs")
    assert cli.enable_final_brief_cache() == "/tmp/my-briefs"
