"""cli — CLI 入口点。"""

import typer

app = typer.Typer(name="ato", help="Agent Team Orchestrator")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Agent Team Orchestrator — 多角色 AI 团队编排系统。"""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
