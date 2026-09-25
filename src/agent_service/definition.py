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
    service: Literal["gmail", "google-sheets", "google-calendar", "github"]
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

    @model_validator(mode="after")
    def _read_only(self) -> AskStep:
        if self.uses and set(self.uses.actions) - {"search", "open", "read"}:
            raise ValueError(f"{self.name}: Ask steps can only read; move {self.uses.actions} to an Act step")
        return self


class BuiltInStep(Step):
    kind: Literal["built-in"]
    operation: dict[str, Any]                # exactly one of: tidy, lookup, filter-rows, compare, three-way-match, show
    takes: Takes = Field(default_factory=dict)
    uses: Uses | None = None
    reruns_by_itself: bool = False

    @field_validator("operation")
    @classmethod
    def _one_operation(cls, v: dict[str, Any]) -> dict[str, Any]:
        known = {"tidy", "lookup", "filter-rows", "compare", "three-way-match", "show"}
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

    @model_validator(mode="after")
    def _inside(self) -> FreeFormBlock:
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.name}: step ids must be unique")
        return self


class BranchPath(Strict):
    name: str
    when: str | None = None                  # CEL; the last path has none (Otherwise)
    then: str                                # end | next | a step id


class BranchBlock(Step):
    kind: Literal["branch"]
    paths: list[BranchPath]

    @model_validator(mode="after")
    def _otherwise_last(self) -> BranchBlock:
        if not self.paths or self.paths[-1].when is not None or any(p.when is None for p in self.paths[:-1]):
            raise ValueError(f"{self.name}: every path but the last needs a condition, and the last (Otherwise) none")
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
    follows_dry_run: str | None = None          # a yes/no run option; none: it always makes its changes

    @model_validator(mode="after")
    def _one_action(self) -> ActStep:
        if (self.create_events is None) == (self.add_row is None):
            raise ValueError(f"{self.name}: an Act step does exactly one thing: create_events or add_row")
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
