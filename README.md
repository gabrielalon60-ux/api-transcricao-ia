# Antigravity Governance Bundle

Copy this structure to the repository root:

```text
.agents/
├── agents.md
├── AGENT_PROTOCOL.md
├── CURRENT_STATE.md
├── PRD.md
├── IMPLEMENTATION_PLAN.md
├── TASKS_TESTS_GATES.md
└── rules/
    └── project-governance.md
```

## Why `.agents`?

Current Google Antigravity documentation uses `.agents/` as the default workspace customization directory.

Workspace Rules live in:

`.agents/rules/`

Antigravity still supports the older `.agent/rules/` path for backward compatibility, but `.agents/rules/` is the current default.

## Important setup step

In Antigravity, open:

`Agent panel → ... → Customizations → Rules`

Create/select the workspace rule `project-governance` and set it to **Always On**.

The file `rules/project-governance.md` references the protocol/state and instructs the agent how to use PRD, Plan and Tracker.

## About `agents.md`

Google's Antigravity Codelab demonstrates `.agents/agents.md` for defining project agent/persona roles.

For persistent behavioral constraints, however, the official Rules mechanism is `.agents/rules/*.md`.

This bundle therefore uses both:

- `.agents/agents.md` → logical project roles;
- `.agents/rules/project-governance.md` → persistent always-on governance.

## Git

Commit the entire `.agents/` directory.

Do not store secrets in these files.
