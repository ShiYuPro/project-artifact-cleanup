# Project Artifact Cleanup

Stop AI-generated scratch files from piling up. A Codex and Claude Code skill with deletion previews, configurable retention, pinning, and recovery before expiry.

## See it work

Run a self-contained demo from this repository with Python 3.9+:

```sh
python3 scripts/demo.py
```

It uses the real CLI with generated sample files in a temporary directory:

```text
1. Created one 13-byte scratch file in a managed task; app.py stays outside.
2. Zero-day preview: would_delete_bytes=13, payload_deleted=false.
3. Retained for 7 days; sweep skips the task because it has not expired.
4. Recovered identical bytes; original pinned and skipped by the next sweep.
5. app.py unchanged. PASS. Removing only this demo's temporary fixtures.
```

The demo checks each result and exits with an error if it differs. It does not
scan an existing project. Its generated fixtures are removed when it exits.
This demonstrates the helper's behavior; deciding which real files are disposable
still requires your judgment. [Run the commands yourself](references/setup.md).

## Install

With [Skills CLI](https://skills.sh/docs) (Node.js and npm required):

```sh
npx skills add ShiYuPro/project-artifact-cleanup --skill project-artifact-cleanup
```

Choose Codex or Claude Code when prompted. This installs into the current project;
review the destination if you already have this skill installed.

Or install directly with Git:

From your project directory, choose the command for your agent. Existing destinations
are not overwritten by `git clone`.

**Codex:**

```sh
git clone https://github.com/ShiYuPro/project-artifact-cleanup.git .agents/skills/project-artifact-cleanup
```

**Claude Code:**

```sh
git clone https://github.com/ShiYuPro/project-artifact-cleanup.git .claude/skills/project-artifact-cleanup
```

Invoke `$project-artifact-cleanup` in a new task. Discovery depends on your host's support for
`SKILL.md`; installation does not change project policy or grant external permissions.

## First use

> Use $project-artifact-cleanup to preview agent-created artifacts in this project. Do not delete anything yet.

Run helper commands from the installed skill directory or this repository root.
See [setup and examples](references/setup.md) and the [full skill](SKILL.md).

## Requirements

Python 3.9+ on macOS, Linux or WSL. No dependencies; native Windows is not supported.

## Verify locally

```sh
python3 scripts/check.py
```

Tests use temporary fixtures and mocked responses. They do not clean your project,
call paid model APIs, or deploy a production service.

## Boundaries

Only explicitly enrolled disposable artifacts are managed. Active, pinned, changed and unsafe groups are protected. Zero-day cleanup and sweeps preview by default. Recovery works only before deletion. No scheduler is installed and installation grants no deletion authority. File classification remains an agent/user responsibility.

## Sources and license

See [SOURCES.md](SOURCES.md) for reviewed alternatives and adaptation decisions,
and [LICENSE](LICENSE) for terms. This standalone repository was split from
[Agent Workflow Skills](https://github.com/ShiYuPro/agent-workflow-skills).
Future changes for this skill belong here.

## Feedback and related work

[Report a problem or first-use blocker](https://github.com/ShiYuPro/project-artifact-cleanup/issues/new/choose).
Include your agent, operating system, command, expected result and actual result;
use a small disposable example and remove private paths, logs and credentials.

[More agent skills](https://github.com/ShiYuPro/agent-workflow-skills) ·
[Creator and projects](https://shiu.pro/) ·
[App and website collaboration](https://shiu.pro/work-with-me/)

For job opportunities or cofounder conversations, [contact Shiyu Yang](https://shiu.pro/contact/).
