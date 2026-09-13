"""Native Hermes Governor plugin. Does not patch or replace host code."""


def register(ctx):
    from .governor import Governor, SCHEMA
    engine = Governor(ctx)
    # Hermes isolates plugin exceptions; enforcement callbacks must therefore
    # convert policy failures into explicit vetoes rather than raise past it.
    def start(**kw):
        try:
            return engine.start(**kw)
        except Exception:
            return {"completion_policy":{"required":True,"buffer_output":True,"verify_on_stop":True},
                    "context":"Governor initialization failed. Do not execute or claim completion; report the policy failure."}
    def before(**kw):
        try:
            return engine.before(**kw)
        except Exception:
            return {"action":"block","message":"Governor policy failed; execution blocked."}
    def after(**kw):
        try:
            return engine.after(**kw)
        except Exception:
            goal=engine.get(kw.get("session_id", ""))
            if goal is not None:
                goal.state="BLOCKED"
                goal.errors.append("Evidence collection failed; objective cannot be verified.")
    ctx.register_hook("pre_llm_call", start)
    ctx.register_hook("pre_tool_call", before)
    ctx.register_hook("post_tool_call", after)
    ctx.register_hook("pre_verify", engine.before_finish)
    ctx.register_hook("completion_gate", engine.finish)
    ctx.register_tool(name="governor", toolset="governor", schema=SCHEMA,
                      handler=engine.tool, description="Plan and verify an objective using observed evidence.")
    ctx.register_cli_command(name="governor", help="Inspect Governor decision records",
                             setup_fn=engine.cli_setup, handler_fn=engine.cli)
