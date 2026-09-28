"""Starts Conductor with prompt caching on for its Claude model steps.

    <conductor's python> conductor_cached.py run workflow.yaml --input ...

Conductor's Claude provider builds each step's request settings without a cache setting, so every turn of a
step's tool loop, every planner turn and every item of a Parallel block re-sends the same prefix (system prompt,
tool definitions, the conversation so far) at full price. Pydantic AI, which the provider runs on, supports
automatic caching (`anthropic_cache`: one breakpoint that moves to the end of the request as it grows); this
turns it on, then runs Conductor's command line as usual.

A stopgap until Conductor offers it as a setting. It runs in Conductor's own Python (the uv tool environment),
not the service's: it imports nothing from agent_service. If a Conductor release moves the function it wraps, the
run goes ahead without caching and says so on stderr, rather than not running at all.
"""

import sys


def enable_prompt_caching() -> bool:
    try:
        from conductor.providers._pydantic_ai import agent_builder
    except Exception as exc:
        print(f"agent-service: prompt caching not enabled ({exc})", file=sys.stderr)
        return False
    build = getattr(agent_builder, "_build_anthropic_model_settings", None)
    if build is None:
        print("agent-service: prompt caching not enabled (this Conductor has no _build_anthropic_model_settings)", file=sys.stderr)
        return False

    def with_cache(*args, **kwargs):
        settings = build(*args, **kwargs)
        settings.setdefault("anthropic_cache", True)          # 5-minute TTL: a run's steps follow each other closely
        return settings

    agent_builder._build_anthropic_model_settings = with_cache
    return True


def main() -> None:
    enable_prompt_caching()
    from conductor.cli.app import app
    sys.argv[0] = "conductor"
    sys.exit(app())


if __name__ == "__main__":
    main()
