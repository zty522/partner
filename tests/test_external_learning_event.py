import json
from pathlib import Path
from types import SimpleNamespace

import partner.v2.external_learning_events as module


def test_external_scout_uses_three_llm_judgments_and_persists_real_sources(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    workspace = root / "instances/04"
    working = workspace / "state/tasks/scout"
    ctx = SimpleNamespace(workspace=str(workspace), working_dir=str(working),
                          task_instance=SimpleNamespace(working_dir=str(working), workspace=str(workspace)))
    replies = iter([
        json.dumps({"github_query": "agent event sourcing", "paper_query": "agent active learning",
                    "knowledge_gap": "Does feedback change the next action?", "selection_criteria": ["testable"]}),
        json.dumps({"repo_index": 0, "paper_index": 0, "repo_reason": "runtime code",
                    "paper_reason": "matched evidence", "expected_information_gain": "high",
                    "possible_disconfirmation": "no downstream change"}),
        "# 机制\n\n" + ("真实代码与论文证据需要共同约束判断。" * 80)
        + "\n\n## 冲突\n\n摘要不能代替全文。\n\n## 三个想法\n\n1. 建立动作反事实。\n2. 保存失败轨迹。\n3. 用独立评估器。"
        + "\n\n## 可证伪实验\n\n使用相同输入比较下一动作。\n\n## 边界\n\n没有读取论文全文。",
    ])
    monkeypatch.setattr(module, "_llm", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(module, "_github_candidates", lambda *args, **kwargs: [{
        "name": "owner/repo", "url": "https://github.com/owner/repo",
        "clone_url": "https://github.com/owner/repo.git", "description": "runtime", "stars": 10,
    }])
    monkeypatch.setattr(module, "_paper_candidates", lambda *args, **kwargs: [{
        "paper_id": "p1", "title": "Active Learning Agents", "url": "https://example.org/p1",
        "abstract": ("A real abstract about LLM agent feedback and action selection. " * 5), "authors": ["A"],
        "external_ids": {},
    }])

    def clone(_root, _repo):
        path = root / "external/code/discovered/owner_repo"
        path.mkdir(parents=True)
        (path / "README.md").write_text("event feedback changes a later action", encoding="utf-8")
        return {"ok": True, "path": str(path), "status": "cloned"}

    def save(_root, _paper):
        path = root / "external/literature/discovered/p1.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Active Learning Agents\n\nreal abstract", encoding="utf-8")
        return {"ok": True, "metadata_path": str(path), "pdf_path": "", "status": "metadata_saved"}

    monkeypatch.setattr(module, "_clone_repo", clone)
    monkeypatch.setattr(module, "_save_paper", save)
    result = module.atomic_external_knowledge_scout(ctx, {})
    assert result["pdf_generation"].get("ok") is True, result["pdf_generation"]
    assert result["ok"] is True, result
    assert result["llm_trace"]["calls"] == 3
    assert result["business_metrics"]["novel_source_pairs"] == 1
    assert any(Path(path).suffix == ".pdf" and Path(path).stat().st_size > 1024
               for path in result["files"])
    assert (root / "external/insights/discovery_index.jsonl").is_file()
    assert (root / "external/code/discovered/owner_repo/README.md").is_file()


def test_json_parser_ignores_think_prefix():
    assert module._json_object('<think>hidden</think> text {"paper_index": 2}') == {"paper_index": 2}


def test_paper_search_falls_back_after_rate_limit(monkeypatch):
    monkeypatch.setattr(module, "_semantic_scholar_candidates",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(module, "_arxiv_candidates",
                        lambda *args, **kwargs: [{"title": "fallback", "source": "arxiv"}])
    monkeypatch.setattr(module, "_crossref_candidates", lambda *args, **kwargs: [])
    assert module._paper_candidates("agent learning", 3)[0]["source"] == "arxiv"


def test_domain_gate_rejects_superficially_similar_non_agent_paper():
    assert module._domain_relevant_paper({
        "title": "Leaf Segmentation with Model Certainty and Test-Time Augmentation",
        "abstract": "computer vision for counting plants",
    }) is False
    assert module._domain_relevant_paper({
        "title": "Continual Learning for LLM Agents",
        "abstract": "runtime feedback changes tool use " * 12,
    }) is True
    assert module._domain_relevant_paper({
        "title": "Continual Learning for LLM Agents", "abstract": "",
    }) is False


def test_markdown_cleaner_removes_private_reasoning_and_preamble():
    raw = "<think>private chain</think> preamble\n# Research note\n\nEvidence."
    assert module._clean_markdown(raw) == "# Research note\n\nEvidence."
