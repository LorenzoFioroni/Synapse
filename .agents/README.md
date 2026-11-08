# Shared coding roles

The role descriptions in `roles/*.md` and the model/effort mappings in `config.json` are the source
of truth. They are repository development helpers, not Synapse runtime plugins or access to email.

| Name | Job | Profile |
|---|---|---|
| Clara | Clarifies, coordinates and returns one result; handles small tasks directly | standard |
| Brie | Distills a long request into a brief with acceptance criteria | brief |
| Cody | Implements a bounded code change | standard |
| Archie | Reviews architecture, migrations and deployment contracts | deep |
| Seth | Reviews security-sensitive changes and scanner findings | deep |
| Vera | Verifies behavior, tests and browser coverage | standard |
| Iris | Runs manually requested triage and files actionable, deduplicated issues | brief |

Clara is the default role in AGENTS.md. Do not load every role for every task. Brie is optional;
simple tasks should not go through a briefing/planning/review ceremony. Clara may delegate bounded
work to at most two active specialists. The parent owns completion and overlapping edits have one
writer. Subagents should not recursively delegate. These instructions authorize appropriate
delegation during repository work; they never override the host's permissions or restrictions.

## Codex and Claude

Run `python tools/agents.py` after changing a role or profile. It generates checked-in
`.codex/agents/*.toml` and `.claude/agents/*.md` adapters. Claude's project settings select Clara
as the main agent; Codex's main conversation follows the Clara instruction in AGENTS.md, and can
use the named custom agents for delegation. No background service, API key or orchestrator is added.
`python tools/agents.py --check` detects drift without writing; local verification and CI run it.

Current profiles map brief work to Luna/Haiku, normal work to Sol/Sonnet, and deep review to
high-effort Sol/Opus. Models and effort controls are provider-specific, not equivalent intelligence
scales. Change their mappings in one JSON file for your account and client. The main Codex model
remains the user's selection; named-agent profiles apply when those agents are invoked. Account or
workspace controls may restrict/override settings. Older clients may not support custom agents or
per-agent effort: use the shared Markdown roles sequentially, or update the client. Reopen the client
after adding definitions. Validate actual model selection in each client during the pilot.

Role instructions such as "do not edit" are workflow boundaries, not security isolation. Host
permissions still apply. Claude/Codex tool names and enforcement differ; credentials and private
client settings belong outside the public repository. The generator preserves unrelated Claude
project settings; do not store secrets there. No global user settings are modified.

## Manual triage

Ask Clara (in either client):

> Have Iris triage Synapse since <last-reviewed commit>, report at most three verified findings,
> and open GitHub issues for actionable findings that are not already tracked. Do not change code.

Omit the commit to inspect the last seven days. This invocation authorizes issue creation, not code
changes. Iris searches issues/PRs again before filing, avoids duplicate reports and returns issue
links. Sensitive security findings are reported privately, without public exploit details. If the
GitHub connector/CLI is unavailable, Iris returns drafts and explicitly reports that no issues were
created. There is no scheduled AI task and no issue is opened merely by installing these roles.

For a four-run pilot, record the reviewed revision, findings and issue links, elapsed time and
reported token usage when available. Keep the report outside the public checkout. Compare total
tokens/time per completed change, including review and rework, against the prior workflow; reduce
delegation if handoffs cost more than they save. Do not estimate token usage when it is unavailable.

Adapter formats: [Codex](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Claude Code](https://code.claude.com/docs/en/sub-agents).
