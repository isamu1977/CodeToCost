"""Proxy export for agent_dispatcher."""
from redsun.services.agent_dispatcher import (
    notify_macos,
    find_hermes_binary,
    dispatch_hermes_agent,
    dispatch_background,
    trigger_gate_1_5,
    trigger_handoff_gate,
    trigger_stage_4_5,
)

__all__ = [
    "notify_macos",
    "find_hermes_binary",
    "dispatch_hermes_agent",
    "dispatch_background",
    "trigger_gate_1_5",
    "trigger_handoff_gate",
    "trigger_stage_4_5",
]
