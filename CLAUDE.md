# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AutoMD is an AI-agent-based molecular dynamics (MD) simulation workflow orchestration system. It uses Microsoft AutoGen's multi-agent group chat to coordinate specialized agents that perform protein preparation, ligand parameterization, molecular docking, post-docking analysis, and MD simulation.

## Environment Setup

Two conda environments are required:

```bash
conda env create -f environment.yml       # Python 3.10 main environment (AutoMD)
conda env create -f environment-mgltools.yml  # Python 2.7 for MGLTools (prepare_receptor4.py)
```

External tool dependencies (not in conda):
- `dock_tools/` — contains P2Rank for pocket prediction. Download separately (see README).
- `memory/RAG/local_sbert_model/` — sentence-transformers model for RAG embeddings. Download separately.

Required API keys in `.env`:
- `LLM_API_KEY`, `LLM_MODEL_ID`, `LLM_BASE_URL` — LLM provider (default: DeepSeek)
- `SERPAPI_API_KEY` — for web search tool

## Running

```bash
# Web UI (Gradio)
python ui_app.py

# CLI mode
python main.py "your task description here"
python main.py --debug-tool-calls "your task"
```

## Architecture

### Multi-Agent System

The project uses AutoGen's `SelectorGroupChat` where a **Coordinator** agent (project manager) delegates work to specialized agents. The group chat is defined in `main.py:GroupChatOrchestrator.__init__`.

**Agent → Prompt → Tool allowlist mapping:**

| Agent | Prompt file | Tools |
|-------|-----------|-------|
| `env_setup_agent` | `Prompts/set_env_agent_prompt.txt` | setup_environment, web_search, read/write/run_shell |
| `protein_pre_agent` | `Prompts/protein_pre_agent_prompt.txt` | fetch_pdb, prepare_pure_protein, run_prepare_receptor4_py, get_protein_ensemble, web_search, read/write/run_shell |
| `ligand_pre_agent` | `Prompts/ligand_pre_agent_prompt.txt` | prepare_ligand_amber_route, web_search, read/write/run_shell |
| `dock_agent` | `Prompts/dock_agent_prompt.txt` | get_docking_box_from_p2rank, dock, setup_environment, web_search, read/write/run_shell |
| `postdock_agent` | `Prompts/postdock_agent_prompt.txt` | analyze_interactions, cluster_docking_poses, generate_interaction_diagram, predict_admet, read/write/run_shell |
| `md_agent` | `Prompts/md_agent_prompt.txt` | run_md_simulation, read/write/run_shell |
| `memory_agent` | `Prompts/memory_agent_prompt.txt` | read_text_file, write_text_file |

### Key Files and Their Roles

- **`main.py`** — Entry point. `GroupChatOrchestrator` wires agents, event store, long-term memory, RAG pipeline, and runs the `SelectorGroupChat`. `run_pipeline()` is the async function called by `ui_app.py`.
- **`Agents/common.py`** — Factory functions: `create_model_client()` reads `.env` and returns an `OpenAIChatCompletionClient`; `create_executor_agent()` builds an `AssistantAgent` from a prompt file + tool allowlist.
- **`Agents/dsml_bridge.py`** — Workaround for DeepSeek models that return DSML pseudo-protocol text instead of proper AutoGen tool calls. `run_agent_with_dsml_visualization()` parses DSML blocks, executes tools, and feeds results back.
- **`tools/use_tools.py`** — `TOOL_MAP` dict maps tool names (strings) to Python functions. `load_tool_registry()` parses `Prompts/tool_registry.json` (supports nested includes/categories). `build_tool_description()` generates structured tool descriptions for LLM prompts.
- **`Prompts/tool_registry.json`** — Top-level tool index referencing individual JSON files in `Prompts/tool_registry/`. Each file defines `name`, `function` (key into TOOL_MAP), `description`, and `parameters`.
- **`orchestration/event_store.py`** — `StructuredEventStore` logs every agent message, tool call, and Coordinator assignment as JSONL events with `run_id` timestamps.
- **`orchestration/long_memory.py`** — `LongMemoryMaterializer` converts event streams into run summaries (`runs/run_*.json`), updates `index.json`, builds RAG chunks via `RunChunkBuilder`, and updates the ChromaDB vector index.
- **`orchestration/reporting.py`** — `ProgressReportBuilder` maintains `output/progress_report.md` (updated in real-time during a run) and generates final execution reports.
- **`orchestration/memory_retriever.py`** — `MemoryRetriever` provides keyword/token-based fallback retrieval from `index.json` when the RAG vector DB is unavailable.
- **`orchestration/chunk_builder.py`** — `RunChunkBuilder` converts run memory JSONs into typed chunks (`task_flow`, `outcome_summary`, `tool_param`) for RAG ingestion.
- **`memory/RAG/chunk_rag.py`** — `ChunkRAGPipeline` wraps ChromaDB + sentence-transformers for vector-based retrieval.
- **`memory/creat_RAG.py`** — CLI script to manage RAG chunks: update, rebuild vector index, or delete a run from all storage.

### Agent Creation Pattern

All agents follow the same pattern via `Agents/common.py:create_executor_agent()`:
1. A prompt file in `Prompts/` defines the agent's system message
2. An allowlist of tool function names determines which tools the agent can call
3. `Agents/<name>_agent.py` provides `create_<name>_agent(model_client)` and `execute_<name>_task(task)` functions

### Tool Registration Pattern

Adding a new tool requires changes in three places:
1. Implement the Python function in `tools/<module>.py`
2. Register the function in `TOOL_MAP` in `tools/use_tools.py`
3. Create a JSON metadata file in `Prompts/tool_registry/` and reference it from `Prompts/tool_registry.json`
4. Add the function name to the relevant agent's allowlist in `Agents/<name>_agent.py`

### Workflow

The standard pipeline order (defined in `Prompts/Organizer.txt`):
```
Protein Preparation → Ligand Preparation → Docking Box → Docking → Post-Dock Analysis (optional) → MD Simulation (optional)
```

The Coordinator receives the user task, retrieves progress history + RAG context, then orchestrates agents via `SelectorGroupChat`. Each agent's output is logged as events. At run completion, events are materialized into long-term memory and RAG chunks.

### Memory System

- **Short-term**: `output/progress_report.md` — updated live during a run, serves as context for the Coordinator's decisions.
- **Long-term**: After each run, events → `runs/run_*.json` → chunks (`chunks/chunks.jsonl`) → ChromaDB vector index. The Coordinator's `search_from_rag` tool retrieves relevant past experience.
- **Fallback**: `MemoryRetriever` provides keyword-based search from `index.json` when RAG is unavailable.

## Engineering Constraints

- The `.env` file must not be committed (currently tracked — needs remediation).
- The model client uses `family="unknown"` in `ModelInfo` because the installed AutoGen OpenAI client cannot round-trip DeepSeek's thinking-mode/reasoning_content reliably.
- `dsml_bridge.py` exists specifically because DeepSeek models sometimes return DSML-formatted pseudo tool calls that bypass AutoGen's native tool handling.
- `pdb4amber`, `tleap`, `antechamber`, `parmchk2` must be on PATH (provided by AmberTools in the conda environment).
- `vina` must be on PATH for docking.
- MGLTools runs in a separate Python 2.7 conda environment; `run_prepare_receptor4_py` calls it via `conda run -n mgltools`.
