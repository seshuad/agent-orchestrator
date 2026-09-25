"""Agent Orchestration Service prototype.

    definition   the agent format builders edit (through the designer)
    compiler     agent definition -> Conductor YAML + limits spec
    runtime      what a run worker ships: gateway shim, Built-in step library, CEL evaluator
    cli          `agent-service compile | run`
"""
