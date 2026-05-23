"""Partner Demo - showcase the key features.

Run this to see Partner in action. Creates a demo workspace,
adds tasks, simulates research cycles, and shows the conversation.

Usage:
    python demo.py
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime

# Add partner to path
sys.path.insert(0, os.path.dirname(__file__))

from partner.task_queue import TaskQueue, Task
from partner.knowledge import KnowledgeBase, KnowledgeEntry
from partner.journal import Journal, JournalEntry
from partner.state import StateManager
from partner.conversation import ConversationEngine


def demo():
    """Run a complete Partner demo."""
    
    # Create demo workspace
    workspace = tempfile.mkdtemp(prefix="partner_demo_")
    state_dir = os.path.join(workspace, "state")
    os.makedirs(state_dir, exist_ok=True)
    
    print("=" * 60)
    print("  🤝 Partner Demo")
    print("  Your AI Research Companion")
    print("=" * 60)
    print()
    
    # Initialize components
    tq = TaskQueue(os.path.join(state_dir, "task_queue.json"))
    kb = KnowledgeBase(os.path.join(state_dir, "knowledge.json"))
    journal = Journal(os.path.join(state_dir, "journal.jsonl"))
    sm = StateManager(state_dir)
    conv = ConversationEngine(journal, kb, tq, sm)
    
    # ── Simulate research activity ──
    print("📖 Simulating 3 days of autonomous research...")
    print()
    
    # Day 1: Literature search
    tasks_done = [
        ("literature_search", "搜索转录组年龄预测最新方法", 
         "Found OMICmAge (Nature Aging 2024): multi-omics clock with MAE < 5 years. "
         "DeepMAge: deep learning transcriptomic clock, MAE = 4.2 in blood.",
         [("findings", "OMICmAge: multi-omics age clock", 
           "Integrates transcriptomics, proteomics, metabolomics, and epigenomics. "
           "MAE < 5 years on independent validation set.", "Nature Aging 2024"),
          ("findings", "DeepMAge: deep learning age prediction",
           "DNN-based cross-tissue transcriptomic age predictor. "
           "MAE ≈ 4.2 years in blood transcriptomics.", "Aging 2024")]),
        
        ("project_scan", "分析年龄预测探索树结果",
         "16 exploration nodes analyzed. Best: age-matched + age-aware correction, "
         "MAE=5.95 (10.5% improvement over baseline 6.65).",
         [("methods", "Age-matched + age-aware batch correction",
           "Filter external samples by age distribution, then apply ComBat/Limma "
           "with age as covariate. Best strategy from 16-node exploration.", "exploration_tree")]),
        
        ("idea_generation", "设计因果推断特征选择实验",
         "Proposed using PC algorithm/NOTEARS to replace correlation-based "
           "feature selection. Expected: more robust features, better generalization.",
         [("concepts", "Causal feature selection for age prediction",
           "Replace Pearson correlation with causal discovery (PC/NOTEARS). "
           "Select genes with direct causal relationship to aging.", "idea_generation")]),
        
        ("deep_dive", "深入研究扩散模型在分子生成中的应用",
         "25+ papers found on diffusion models for drug design. "
         "Key: discrete diffusion replacing VAEs. ProDCARL uses RL-aligned diffusion for AMP design.",
         [("findings", "Diffusion models replacing VAEs in molecular generation",
           "Discrete diffusion (D3PM, EvoDiff) outperforms VAEs in diversity and quality. "
           "RL alignment (ProDCARL) boosts AMP hit rate from 2% to 6.3%.", "arXiv 2025-2026")]),
        
        ("knowledge_synthesis", "整合批次校正方法论",
         "Comprehensive batch correction methodology document created from "
           "16-node exploration tree results.",
         [("methods", "Batch correction best practices",
           "Age-matching + age-aware correction optimal. SVA/quantile normalization "
           "over-correct and destroy age signal. Simple linear methods most robust.", "exploration_tree")]),
    ]
    
    for task_type, title, summary, knowledge_entries in tasks_done:
        # Create and complete task
        task = Task(type=task_type, title=title, description=title, priority=7)
        tq.add_task(task)
        tq.complete(task.id, summary)
        
        # Add knowledge
        for cat, ktitle, content, source in knowledge_entries:
            kb.add(KnowledgeEntry(
                category=cat, title=ktitle, content=content,
                source=source, confidence="high",
                tags=[task_type],
            ))
        
        # Log
        journal.log(JournalEntry(
            task_id=task.id, task_type=task_type,
            task_title=title, result_summary=summary,
            knowledge_entries_added=len(knowledge_entries),
        ))
    
    # Update stats
    sm.update_stats({
        "total_cycles": 5,
        "total_tasks_completed": 5,
        "total_knowledge_entries": len(kb.entries),
        "last_run": datetime.now().isoformat(),
    })
    sm.heartbeat(status="idle")
    
    # Add pending tasks
    pending_tasks = [
        ("deep_dive", "研究 scGPT 微调用于年龄预测", 9),
        ("idea_generation", "设计扩散模型替代 VAE 方案", 8),
        ("literature_search", "搜索 GrimAge 最新版本", 7),
        ("cross_project", "分析分子生成方法跨项目复用", 7),
        ("skill_learning", "学习 MOFA+ 多组学因子分析", 6),
    ]
    for ptype, ptitle, pprio in pending_tasks:
        tq.add_task(Task(type=ptype, title=ptitle, priority=pprio))
    
    print("✅ Research simulation complete!")
    print()
    
    # ── Show status ──
    print("=" * 60)
    print("  📊 partner status")
    print("=" * 60)
    print()
    
    stats = sm.load_stats()
    print(f"  ⏱  Cycles:          {stats.get('total_cycles', 0)}")
    print(f"  📋 Tasks completed:  {stats.get('total_tasks_completed', 0)}")
    print(f"  📚 Knowledge:        {len(kb.entries)} entries")
    print(f"  ⏳ Pending tasks:    {len([t for t in tq.tasks if t.status == 'pending'])}")
    print()
    
    # ── Show conversation ──
    print("=" * 60)
    print("  💬 Demo Conversation")
    print("=" * 60)
    
    queries = [
        "Hey Partner, what have you been doing?",
        "What do you know about diffusion models?",
        "What's coming up next?",
        "Help",
    ]
    
    for query in queries:
        print(f"\n  You: {query}")
        print(f"  {'─' * 50}")
        response = conv.respond(query)
        # Indent response
        for line in response.split("\n"):
            print(f"  Partner: {line}")
    
    print()
    print("=" * 60)
    print("  🎉 Demo complete!")
    print()
    print("  To try Partner yourself:")
    print("    git clone https://github.com/zty522/partner.git")
    print("    cd partner && pip install -e .")
    print("    partner setup")
    print("=" * 60)
    
    # Cleanup
    shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    demo()
