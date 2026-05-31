## v0.5.0 - Autonomous Runtime Refactor

This release tag is repointed to the current `main` branch implementation.

### What changed

- Rebuilt Partner around two explicit lines:
  - `mind_loop` as the autonomous lifeline
  - `InteractionOrchestrator` as the user-message decision layer
- Moved runtime data to isolated multi-instance workspaces under `partner_workspace/instances/{id}/`
- Added per-instance Hermes runtime homes so logs, auth, and caches no longer clash
- Refactored QQ message handling to separate:
  - user reply generation
  - lifeline mutation decisions
  - proactive progress reporting
- Fixed duplicate replies, raw heartbeat JSON leakage, repeated status replies, and stale bridge processes
- Changed project execution into small structured steps with artifact write-back:
  - `DONE`
  - `FINDINGS`
  - `NEXT`
  - `STATE_DELTA`
  - `ARTIFACT_CONTENT`
- Verified runtime artifact generation in real instance workspaces:
  - `01` age prediction recovery notes
  - `03` Acinetobacter next experiment notes
  - `04` agent benchmark / evaluation framework notes
- Normalized multi-instance workspace layout for both system-facing and user-facing records

### Runtime architecture

- **Lifeline:** autonomous `mind_loop` keeps projects moving without waiting for chat
- **Interaction line:** user messages go through a lightweight LLM that returns:
  - a natural-language reply
  - a structured lifeline mutation decision
- **Execution line:** project work is executed as one small step at a time, then written back into project files

### Workspace model

Each instance now runs from its own workspace, for example:

- `partner_workspace/instances/01`
- `partner_workspace/instances/03`
- `partner_workspace/instances/04`

Each project keeps natural-language state and exploration records in its own folder.

### Windows installer

The GitHub Actions workflow for tag pushes rebuilds the Windows installer and uploads the new EXE asset to this release.

### Notes

- This release replaces the earlier `v0.5.0` tag contents with the current `main` implementation.
- README has been updated to match the new dual-line runtime and workspace layout.
