"""The agent format (agent-service/v1): the document the designer saves and the compiler reads.

An agent is a trigger, run options, limits, connections, record types and a list of steps.
Steps are Ask, Built-in, Approve and Act; flow blocks are Branch and Free-form. Every rule is
CEL. Inputs are picked, never typed: a step's `takes` maps each input name to a reference.

References
    <step>.<field>[.<field>...]     an earlier step's output (a Free-form block's `returns` too)
    <step>.<list>[*].<field>        every item's field, flattened into one list
    planner.<field>                 inside a Free-form block: what its planner decided
    collected.<name>                inside a Free-form block: results kept across repeated runs
    run.<option>                    a run option
    trigger.email_id | trigger.sender_domain    the email that started the run
    a trailing `?` makes an input optional; a list means "any of these"

CEL rules read `steps.<id>` (null until that step has run), `planner`, `collected`, `run`, and in
Approve's pre-selection `item`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FORMAT = "agent-service/v1"
SCALAR_TYPES = {"text", "number", "yes/no", "date & time with time zone"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class FieldDef(Strict):
    type: str
    of: list[str] | None = None            # choice values
    optional: bool = False
    hint: str | None = None
    when_type: str | None = None            # extra field that applies to one kind of record


class RecordType(Strict):
    fields: dict[str, FieldDef]
    identity: str | None = None             # CEL over `b`: the same record when this matches


class RunOption(Strict):
    type: Literal["yes/no", "text", "number"]
    default: Any = None
    description: str | None = None


class Trigger(Strict):
    kind: Literal["schedule", "email", "webhook", "manual"]
    every: str | None = None
    at: str | None = None
    time_zone: str | None = None
    to: str | None = None


class Limits(Strict):
    budget_usd: float
    timeout_minutes: int | None = None


class Connection(Strict):
    service: Literal["gmail", "google-sheets", "google-calendar", "github", "mcp", "bigquery"]
    permission: str
    account: str | None = None               # the workspace connection (account) it uses


class Uses(Strict):
    """One use of a connection by one step, with the limits the gateway enforces."""
    connection: str
    actions: list[str]
    senders: list[str] | None = None
    lookback_days: int | None = None
    only_message: str | None = None          # e.g. trigger.email_id
    from_domain: str | None = None           # e.g. trigger.sender_domain
    only_cited_by: str | None = None         # a step id: only emails its output names
    sheets: list[str] | None = None
    calendar: str | None = None
    repos: list[str] | None = None           # GitHub: the repositories (owner/name) it may read
    arg_limits: dict[str, list[str]] | None = None   # MCP: argument -> the only values a call may pass
    datasets: list[str] | None = None        # BigQuery: dataset, project.dataset or project.dataset.table it may read
    max_bytes: str | int | None = None       # BigQuery: the most one query may scan, e.g. "1GB"
    max_rows: int | None = None              # BigQuery: rows returned per query
    tables: list[str] | None = None          # BigQuery: tables an Act step may insert into


class Repeat(Strict):
    planner_sets: str | None = None          # the input the planner may change on each run
    usually_after: str | None = None         # a hint for the planner, and where the dotted line starts
    when: str | None = None


Takes = dict[str, Union[str, list[str]]]


class Step(Strict):
    id: str
    name: str

    @field_validator("id")
    @classmethod
    def _identifier(cls, v: str) -> str:
        if not v.replace("_", "").isalnum() or not v[0].isalpha() or v.lower() != v:
            raise ValueError("step ids are lower case letters, digits and underscores, starting with a letter")
        return v


class Instructions(Strict):
    shared: str
    extra: str | None = None


class AskStep(Step):
    kind: Literal["ask"]
    model: str
    takes: Takes = Field(default_factory=dict)
    uses: Uses | None = None
    repeat: Repeat | None = None
    instructions: str | Instructions
    task: str
    returns: dict[str, FieldDef]



class BuiltInStep(Step):
    kind: Literal["built-in"]
    operation: dict[str, Any]                # exactly one of: tidy, lookup, filter-rows, compare, three-way-match, show, javascript
    takes: Takes = Field(default_factory=dict)
    uses: Uses | None = None
    reruns_by_itself: bool = False
    returns: dict[str, FieldDef] = Field(default_factory=dict)   # javascript: the fields the code returns

    @field_validator("operation")
    @classmethod
    def _one_operation(cls, v: dict[str, Any]) -> dict[str, Any]:
        known = {"tidy", "lookup", "filter-rows", "compare", "three-way-match", "show", "javascript", "bigquery"}
        if len(v) != 1 or next(iter(v)) not in known:
            raise ValueError(f"operation must be exactly one of {sorted(known)}")
        return v

    @property
    def op(self) -> str:
        return next(iter(self.operation))


class Rule(Strict):
    rule: str
    message: str


class FreeFormBlock(Step):
    kind: Literal["free-form"]
    goal: str
    planning_model: str
    limits: dict[Literal["ask_runs", "turns"], int]
    outcomes: list[str] | None = None
    planner_returns: dict[str, FieldDef] = Field(default_factory=dict)
    collect: dict[str, list[str]] = Field(default_factory=dict)
    steps: list[Annotated[Union[AskStep, BuiltInStep], Field(discriminator="kind")]]
    before_finishing: list[Rule] = Field(default_factory=list)
    returns: dict[str, str]                  # name -> CEL
    memory: Memory | None = None             # past confirmed investigations, shown to the planner

    @model_validator(mode="after")
    def _inside(self) -> FreeFormBlock:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.name}: step ids must be unique")
        return self


class Memory(Strict):
    """Past cases a person confirmed, shown to a judgment (a model-decided Branch, a Free-form planner) before it decides."""
    match_on: dict[str, str] = Field(default_factory=dict)   # name -> reference: what makes two cases similar
    max_cases: int = 5
    ask_sample: float = 0.05                 # besides the unsure ones, the share of decisions a person is asked to check

    @field_validator("ask_sample")
    @classmethod
    def _share(cls, v: float) -> float:
        if not 0 <= v <= 1:
            raise ValueError("ask about between 0% and 100% of the decisions it's sure of")
        return v


class BranchPath(Strict):
    name: str
    when: str | None = None                  # rules: CEL; the last path has none (Otherwise)
    when_true: str | None = None             # model: when this path applies, in words; the last path is the safe default
    then: str = "next"                       # end | next | a step id; deciding for each item, every path goes on


class HardRule(Strict):
    when: str                                # CEL, checked before the model is asked
    then: str


class ForEach(Strict):
    """A model-decided Branch that decides once per item of a list, in parallel, instead of once per run."""
    over: str                                # a reference to a list, e.g. list_issues.issues
    as_: str = Field("item", alias="as")     # what one item is called: in the question, and in memory's fields
    at_once: int = 5                         # how many items are decided at the same time

    @field_validator("as_")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v.isidentifier() or v in {"run", "trigger", "steps", "planner", "collected", "each"}:
            raise ValueError(f"{v!r} can't name an item: use a plain word such as issue or invoice")
        return v


class BranchBlock(Step):
    kind: Literal["branch"]
    paths: list[BranchPath]
    decide: Literal["rules", "model"] = "rules"
    model: str | None = None                 # model: which one decides
    question: str | None = None              # model: the question it answers
    takes: Takes = Field(default_factory=dict)          # model: what it decides on
    rules_first: list[HardRule] = Field(default_factory=list)   # model: outcomes that aren't up to judgment
    memory: Memory | None = None             # model: past confirmed decisions to learn from
    uses: Uses | None = None                 # model: what it may read to decide (read-only actions)
    for_each: ForEach | None = None          # model: decide once per item of a list

    @model_validator(mode="after")
    def _otherwise_last(self) -> BranchBlock:
        if not self.paths:
            raise ValueError(f"{self.name}: a Branch needs at least one path")
        if self.decide == "rules":
            if self.paths[-1].when is not None or any(p.when is None for p in self.paths[:-1]):
                raise ValueError(f"{self.name}: every path but the last needs a condition, and the last (Otherwise) none")
            if self.memory is not None:
                raise ValueError(f"{self.name}: memory informs a judgment; a rule-based Branch makes none. Decide with a model to use memory.")
            if self.for_each is not None or self.uses is not None:
                raise ValueError(f"{self.name}: only a Branch decided by a model can decide for each item, or read to decide")
        else:
            if len(self.paths) < 2:
                raise ValueError(f"{self.name}: a model needs at least two paths to choose between")
            missing = [p.name for p in self.paths[:-1] if not (p.when_true or "").strip()]
            if missing:
                raise ValueError(f"{self.name}: say when each path applies: {', '.join(missing)}")
            if not (self.question or "").strip():
                raise ValueError(f"{self.name}: write the question the model decides")
            names = [p.name for p in self.paths]
            if len(set(names)) != len(names):
                raise ValueError(f"{self.name}: path names must be different")
            if self.for_each is not None and self.rules_first:
                raise ValueError(f"{self.name}: hard rules can't be used yet when deciding for each item; filter the list first")
            if self.for_each is not None and not 1 <= self.for_each.at_once <= 20:
                raise ValueError(f"{self.name}: decide 1 to 20 items at the same time")
        return self


class Choice(Strict):
    label: str
    passes: Literal["none", "pre-selected", "all", "picked"]


class ApproveStep(Step):
    kind: Literal["approve"]
    approver: str
    notify: list[str]
    timeout_hours: int
    items: str | None = None
    item_id: str | None = None
    pre_select: str | None = None            # CEL over `item`
    review: str                              # text with {reference} placeholders
    choices: list[Choice]

    @model_validator(mode="after")
    def _safe_first(self) -> ApproveStep:
        if self.choices[0].passes != "none":
            raise ValueError(f"{self.name}: the first choice must pass nothing, so a timeout or an unattended run changes nothing")
        return self


class ActStep(Step):
    kind: Literal["act"]
    uses: Uses
    takes: Takes = Field(default_factory=dict)
    create_events: dict[str, Any] | None = None
    add_row: dict[str, Any] | None = None
    call_tool: dict[str, Any] | None = None      # MCP: {tool, arguments: {arg: "{field}" or text}, for_each}
    insert_rows: dict[str, Any] | None = None    # BigQuery: {table, for_each, row: {column: "{field}"}}
    follows_dry_run: str | None = None          # a yes/no run option; none: it always makes its changes

    @model_validator(mode="after")
    def _one_action(self) -> ActStep:
        if sum(x is not None for x in (self.create_events, self.add_row, self.call_tool, self.insert_rows)) != 1:
            raise ValueError(f"{self.name}: an Act step does exactly one thing: create_events, add_row, call_tool or insert_rows")
        return self


AnyStep = Annotated[Union[AskStep, BuiltInStep, FreeFormBlock, BranchBlock, ApproveStep, ActStep], Field(discriminator="kind")]


class Agent(Strict):
    format: Literal["agent-service/v1"]
    name: str
    description: str
    trigger: Trigger
    run_options: dict[str, RunOption] = Field(default_factory=dict)
    limits: Limits
    connections: dict[str, Connection]
    records: dict[str, RecordType] = Field(default_factory=dict)
    shared_instructions: dict[str, str] = Field(default_factory=dict)
    steps: list[AnyStep]

    @model_validator(mode="after")
    def _references(self) -> Agent:
        for s in self.all_steps():
            uses = getattr(s, "uses", None)
            if uses and uses.connection not in self.connections:
                raise ValueError(f"{s.name}: uses connection {uses.connection!r}, which this agent hasn't connected")
            # Built-in services: Ask steps only read. An MCP connector's tools are read or act by its admin's choice,
            # checked against the workspace's connectors when the agent is saved.
            if (isinstance(s, (AskStep, BranchBlock)) and uses and self.connections[uses.connection].service != "mcp"
                    and set(uses.actions) - {"search", "open", "read", "query", "list_tables", "get_schema"}):
                raise ValueError(f"{s.name}: {'Ask steps' if isinstance(s, AskStep) else 'a Branch'} can only read; move {uses.actions} to an Act step")
            if isinstance(s, AskStep) and isinstance(s.instructions, Instructions) and s.instructions.shared not in self.shared_instructions:
                raise ValueError(f"{s.name}: no shared instructions called {s.instructions.shared!r}")
        if sum(isinstance(s, FreeFormBlock) for s in self.steps) > 1:
            raise ValueError("this prototype compiles at most one Free-form block per agent")
        return self

    def all_steps(self) -> list[Any]:
        out: list[Any] = []
        for s in self.steps:
            out.append(s)
            if isinstance(s, FreeFormBlock):
                out.extend(s.steps)
        return out


def load(path: str | Path) -> Agent:
    return Agent.model_validate(yaml.safe_load(Path(path).read_text()))
