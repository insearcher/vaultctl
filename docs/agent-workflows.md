# Agent workflows

`vaultctl` is a deterministic tool used by an agent or human operator. It is
not the owner of Git, editing, approvals, or retry orchestration.

## Composition model

```text
Agent or human
  ├── Git CLI / forge tools: inspect, branch, diff, stage, commit, push
  ├── vaultctl: scan, plan, validate, and future explicit mutations
  └── editor or application CLI: inspect and edit Markdown
```

The agent skill or prompt owns the sequence and domain policy. Consumer
repositories may provide different instructions while using the same
`vaultctl` contracts and manifest model.

## Create/update loop

For one Markdown node, the caller:

1. reads the current graph or note and constructs a versioned create/update
   request;
2. runs `vaultctl node plan --request request.json`;
3. inspects the typed validation issues and `node diff`;
4. uses `node render` as exact candidate input to an explicit editor,
   application, or future separately approved apply command;
5. validates and manages Git with ordinary tools.

Update requests require the current raw source hash. Render and diff repeat
the absence/hash, manifest, engine, candidate, and prospective-vault checks.
An invalid plan remains inspectable so the agent can revise its request, but
it is not authorization to write.

## Conflict-resolution loop

For a Markdown conflict, the caller:

1. obtains the exact base, ours, and theirs inputs with ordinary Git tools;
2. runs `vaultctl merge plan` to get deterministic decisions, candidate
   evidence, and typed conflicts;
3. inspects the evidence and chooses whether to use the candidate, edit with
   an editor/application CLI, or ask for human input;
4. runs `vaultctl merge validate` when the current target still matches the
   plan's `ours` hash, or validates the explicitly edited vault with
   `vaultctl validate`;
5. reviews the diff and uses ordinary Git tools to stage, commit, and push.

An unambiguous candidate is advice, not permission to write or publish.
Concurrent scalar/body changes remain visible to the caller rather than being
silently resolved.

## Prompt boundary

A consumer skill or prompt should specify:

- how to identify the intended vault and repository;
- which Git and application tools are available;
- when a write or publication requires approval;
- which validation commands must pass;
- when to stop on ambiguity or unrelated dirty state.

It should not duplicate parsing, graph, schema, or merge logic that belongs in
`vaultctl`.

## Core non-goals

The core does not:

- install or run Git hooks or custom merge drivers;
- create commits, update refs, or contact remotes implicitly;
- call an LLM or hide agent decisions inside deterministic commands;
- encode consumer-specific prompts, credentials, or authorization;
- provide forge queue, daemon, or autonomous promotion behavior.

Those boundaries keep the CLI reusable and auditable while allowing capable
agents to combine it with Git and application-specific tools.
