"""release_pipeline: composite ADK graph for the lot-release gate.

Exposes `root_agent` per ADK's agent-loader convention. The loader inspects
each subdirectory of agents_dir and imports the module's `root_agent`
(or `agent`) symbol. See google.adk.cli.fast_api._load_agent_from_dir.
"""
from .agent import root_agent

__all__ = ["root_agent"]
