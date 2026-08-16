# Multi-AI Coordination (AGENTS.md) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every AI session that edits the PlexusLab repo directly (Claude Code, Antigravity CLI, or any future tool run manually in this folder) one shared, git-tracked file to announce what it's doing and hand off to the next session.

**Architecture:** A single new markdown file, `AGENTS.md`, at the PlexusLab repo root (`C:\Projects\PlexusLab\AGENTS.md`). No code, no scripts, no hooks — purely a convention document with two living sections that any AI session edits by hand as part of its normal workflow. This does not touch `cortex/runtime/queue.py` or any other runtime code; that lock already covers a different case (`projects/*` workspaces) and is out of scope per the spec.

**Tech Stack:** Markdown only. No dependencies, no build step.

## Global Constraints

- Scope is the PlexusLab repo itself only — not `projects/*` (basehub, financier_ai, etc.). Do not modify `cortex/runtime/queue.py` or any Python source as part of this plan.
- No hard blocking, no lock enforcement, no new scripts/tooling — the spec explicitly rejected that (CEO wants "same wavelength", not a gate).
- The file must be readable as pure documentation by a human, not just by an AI.
- Source of truth for exact required content: `docs/superpowers/specs/2026-08-16-multi-ai-coordination-design.md`. If anything in this plan seems to conflict with that spec, the spec wins.

---

### Task 1: Create AGENTS.md with protocol, active-sessions, and history sections

**Files:**
- Create: `C:\Projects\PlexusLab\AGENTS.md`

**Interfaces:**
- Consumes: nothing (no code dependencies).
- Produces: a file at repo root named exactly `AGENTS.md`, containing three headings in this order: a protocol intro (no fixed heading text required, just prose before the first `##`), `## Сейчас активны`, `## История последних сессий`. Any later task/session appends rows/entries under these two exact headings — do not rename them, other sessions will pattern-match on this exact text.

- [ ] **Step 1: Write the file**

Create `C:\Projects\PlexusLab\AGENTS.md` with exactly this content:

```markdown
# Координация AI-сессий в PlexusLab

Этот репозиторий иногда правят разные AI-сессии напрямую (Claude Code,
Antigravity CLI, другие) — в обход собственного движка Cortex, который
видит только проекты из `projects/*`, но не свой собственный код. Этот
файл — общая память между такими сессиями, ведётся вручную.

**Протокол для любой AI-сессии, начинающей работу в этой папке:**

1. Прочитай раздел «Сейчас активны» ниже. Если там кто-то есть и запись
   свежая (часы, не дни) — не редактируй те же файлы вслепую: спроси CEO
   или дождись, пока запись переедет в «Историю».
2. Начиная нетривиальную задачу — впиши себя в «Сейчас активны»: кто ты,
   с какого момента, что делаешь.
3. Закончив — перенеси свою строку в «Историю» с однострочным итогом,
   либо просто удали её, если правок не было.
4. Если `git status` грязный, а в «Сейчас активны» никого нет — не молчи:
   разберись (`git diff`, `git log`), допиши находку в «Историю» одной
   строкой и только потом продолжай.

## Сейчас активны

_(пусто)_

## История последних сессий

- 2026-08-16 05:04 · Claude Code · чистка мусорных артефактов
  (`cortex/fix.py`, `cortex/test.txt`, `cortex/context/test.txt`,
  старые `data/live_run_final*.log`), правка README (55 → 260 тестов),
  дизайн-документ и план для этого файла.
```

- [ ] **Step 2: Verify the file was written correctly**

Run: `grep -c "^## " "C:\Projects\PlexusLab\AGENTS.md"`
Expected: `2` (exactly two `##` headings: «Сейчас активны» and «История последних сессий»)

Run: `grep -F "Сейчас активны" "C:\Projects\PlexusLab\AGENTS.md"` and `grep -F "История последних сессий" "C:\Projects\PlexusLab\AGENTS.md"`
Expected: both commands print one matching line each — confirms the exact heading text other sessions will pattern-match on is present verbatim.

- [ ] **Step 3: Commit**

```bash
cd "C:\Projects\PlexusLab"
git add AGENTS.md
git commit -m "docs: add AGENTS.md for cross-session AI coordination

Implements docs/superpowers/specs/2026-08-16-multi-ai-coordination-design.md."
```

Expected: commit succeeds, `git log -1 --stat` shows exactly one file added: `AGENTS.md`.

---

## Self-Review Notes

- **Spec coverage:** spec requires (a) one shared git-tracked markdown file — done via Task 1; (b) protocol section — included; (c) "Сейчас активны" section — included, empty by default; (d) "История" section — included, seeded with this session's own work as the first entry (matches spec's stale/empty-active edge case guidance by demonstrating the pattern); (e) explicitly out of scope: `queue.py`/`projects/*` — not touched by this plan.
- **No placeholders:** the full literal file content is given in Step 1, nothing left as TBD.
- **Single task is correct sizing here:** the deliverable (one static file) can't be meaningfully split — there's no intermediate state between "file doesn't exist" and "file exists with correct content" worth its own review gate.
