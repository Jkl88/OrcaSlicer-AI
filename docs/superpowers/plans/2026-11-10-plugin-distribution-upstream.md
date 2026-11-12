# Plugin Distribution and Upstream Delivery Implementation Plan

> **Superseded (2026-11-13)** by `docs/superpowers/plans/2026-11-13-plugins-on-official-v2.5.0.md`. Official v2.5.0 already ships a preset transaction API (#16518), plugin storage and permissions. The fork root is not v2.5.0. The user chose to adopt the upstream API, release plugins only, and make no upstream PR.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible OrcaSlicer v2.5.0 host patch, independently versioned plugin packages, verified patched builds, compatibility metadata, and a clean generic host API pull request for official OrcaSlicer.

**Architecture:** Keep generic host commits separable from plugin commits. Generate the stable patch from the exact official v2.5.0 commit, verify it in clean worktrees, build plugins independently, and adapt only generic API commits to current upstream `main` for the PR.

**Tech Stack:** Git/worktrees, CMake/CTest, GitHub Actions, Python build tooling, JSON Schema validation, GitHub CLI.

**Spec:** `docs/superpowers/specs/2026-11-10-orcaslicer-plugin-host-api-design.md`

**Depends on:**
- `docs/superpowers/plans/2026-11-10-plugin-host-api.md`
- `docs/superpowers/plans/2026-11-10-ai-assist-plugin.md`
- `docs/superpowers/plans/2026-11-10-filament-qr-plugin.md`

## Global Constraints

- Stable patch base is official OrcaSlicer v2.5.0 commit `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`.
- Installed EXE/DLL/application bundles are never binary-patched in place.
- Patch application failure, checksum mismatch, host test failure, or plugin compatibility failure blocks release.
- Plugin packages have independent versions and are not baked into the generic upstream PR.
- The upstream PR is based on current official `main`, not the fork root or release patch branch.
- Upstream commits contain only generic API C++, tests, stubs/examples, and documentation—no Gemini, AI Assist, Telegram, Moonraker, QR, filament inventory, or fork release workflow.
- Outward-facing release/PR publication is done only after final verification; the user already explicitly requested the upstream PR.
- All generated metadata is deterministic and records platform checks honestly; skipped builds are `not-tested`, never `passed`.

## Review Focus

- A patch that applies with offset to the wrong source tree must be rejected by base commit/precondition checks; Task 2 adds the test.
- Compatibility metadata must reject a plugin requiring a feature absent from the packaged host; Task 3 adds schema/semantic tests.
- A release rerun must not overwrite artifacts for a different source commit under the same version silently; Task 4 pins source and patch revision.
- Upstream PR range must contain no provider/plugin-specific strings or files; Task 5 adds an automated deny scan.
- Platform build failures or unavailable runners must remain visible in the manifest/release notes rather than being reported as successful; Task 4 verifies status generation.

---

### Task 1: Validate Official Bases and Isolate Commit Ranges

**Files:**
- Create: `docs/release/plugin-host-api-baselines.md`
- Create: `scripts/check_plugin_host_commit_scope.py`
- Create: `tests/scripts/test_check_plugin_host_commit_scope.py`
- Modify: `.gitignore` only if build/patch scratch outputs need an existing convention

**Interfaces:**
- Produces documented refs: `official-v2.5.0`, current `official-main`, generic host commit range, AI plugin range, filament plugin range.
- Produces: `check_plugin_host_commit_scope.py <base> <head>` returning nonzero for forbidden paths/terms.

- [ ] **Step 1: Fetch/verify official refs without changing the working branch**

Verify commit `40ecc66f...` exists and its tag/remote provenance. Record current official `main` SHA and date. If remote is missing, add a temporary official remote only with user/network permission and document it.

- [ ] **Step 2: Write failing scope-check tests**

Fixtures include allowed generic host/test/docs paths and forbidden `AIAssist`, Gemini, Telegram, Moonraker, `plugins/ai_assist`, `plugins/orca_filament`, QR/inventory terms, release workflows, and unrelated profile/localization files.

- [ ] **Step 3: Implement deterministic scope checker**

Inspect both changed paths and textual diff. Permit motivating use cases only in the upstream PR description, not host source identifiers; docs may mention generic examples without provider branding.

- [ ] **Step 4: Run tests and record baselines**

Run: `python3 -m unittest tests/scripts/test_check_plugin_host_commit_scope.py -v`

Expected: PASS. Baseline document records exact commands and SHAs.

- [ ] **Step 5: Commit**

```bash
git add docs/release/plugin-host-api-baselines.md \
  scripts/check_plugin_host_commit_scope.py tests/scripts/test_check_plugin_host_commit_scope.py
git commit -m "Record plugin API release baselines

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 2: Reproducible v2.5.0 Source Patch

**Files:**
- Create: `patches/v2.5.0/preset-workspace-api.patch`
- Create: `patches/v2.5.0/README.md`
- Create: `scripts/verify_plugin_host_patch.py`
- Create: `tests/scripts/test_verify_plugin_host_patch.py`

**Interfaces:**
- Produces patch applicable to exact base `40ecc66f78ed72dc368e0c1ebe40d77f00cc8a19`.
- Produces verifier: `verify_plugin_host_patch.py --source DIR --manifest patches/compatibility.json --check-only|--apply`.

- [ ] **Step 1: Create an isolated v2.5.0 worktree**

Use `superpowers:using-git-worktrees`. Cherry-pick/adapt only generic host commits from the API plan. Resolve differences with v2.5.0 architecture without pulling plugin/native AI code.

- [ ] **Step 2: Run host tests in the clean stable tree**

Configure/build according to Linux project instructions, run `slic3rutils` host tests, and record exact limitations if full dependencies are unavailable. No patch is generated from an untested dirty tree.

- [ ] **Step 3: Generate a deterministic patch from exact base**

Use `git format-patch --stdout` or a documented deterministic equivalent over the generic commit range. README includes exact base, apply/reverse commands, test commands, and no binary patch instructions.

- [ ] **Step 4: Write verifier tests before implementation**

Test exact base success, wrong commit rejection even when patch could offset-apply, dirty tree rejection, already-applied state, checksum mismatch, dry run, and traversal/unsafe patch paths.

- [ ] **Step 5: Implement verifier and run tests**

Expected: verifier checks `git rev-parse HEAD`, clean status, patch SHA-256 from manifest, and `git apply --check` before apply.

- [ ] **Step 6: Verify patch from a second clean worktree**

Apply with the verifier, build focused target, run host tests, and compare resulting generic source tree to the stable API adaptation.

- [ ] **Step 7: Commit**

```bash
git add patches/v2.5.0 scripts/verify_plugin_host_patch.py \
  tests/scripts/test_verify_plugin_host_patch.py
git commit -m "Add reproducible OrcaSlicer 2.5.0 API patch

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 3: Compatibility Manifest and Plugin Artifacts

**Files:**
- Create: `patches/compatibility.schema.json`
- Create: `patches/compatibility.json`
- Create: `scripts/build_plugin_packages.py`
- Create: `scripts/validate_plugin_compatibility.py`
- Create: `tests/scripts/test_plugin_compatibility.py`
- Modify: `plugins/ai_assist/pyproject.toml`
- Modify: `plugins/orca_filament/pyproject.toml`

**Interfaces:**
- Manifest records official tag/SHA, patch revision/SHA-256, host API version/features, plugin version ranges, source preconditions, artifact hashes, and build/test status per platform.
- Produces deterministic plugin wheels/sdists and checksum file.

- [ ] **Step 1: Write manifest schema and semantic tests**

Test missing required fields, invalid SHA/version ranges/status, duplicate features, plugin requiring absent host feature, stale artifact hash, and explicit `not-tested` platform status.

- [ ] **Step 2: Run tests and verify failure**

Expected: FAIL because validators/manifest are absent.

- [ ] **Step 3: Implement schema plus semantic validator**

JSON Schema handles shape; Python validator handles feature/version compatibility and hashes. Keep source precondition exact for v2.5.0.

- [ ] **Step 4: Implement deterministic plugin package builder**

Build AI and filament packages independently, normalize output naming, calculate SHA-256, and verify wheel metadata/import in isolated temporary environments. Do not install unpinned arbitrary tools from untrusted directories.

- [ ] **Step 5: Generate and validate initial manifest**

Populate only verified platform statuses. Ensure AI manual mode supports stock host and automatic range requires exact feature identifiers; same for filament plugin.

- [ ] **Step 6: Run plugin tests/builds and validator**

Run both plugin suites, package builds, wheel import smoke tests, manifest validation, and `git diff --check`.

- [ ] **Step 7: Commit**

```bash
git add patches/compatibility.schema.json patches/compatibility.json \
  scripts/build_plugin_packages.py scripts/validate_plugin_compatibility.py \
  tests/scripts/test_plugin_compatibility.py plugins/ai_assist/pyproject.toml \
  plugins/orca_filament/pyproject.toml
git commit -m "Define plugin host compatibility manifest

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 4: CI and Patched Release Packages

**Files:**
- Create: `.github/workflows/plugin_host_api.yml`
- Modify: `.github/workflows/build_windows_installer.yml`
- Create: `docs/release/patched-orcaslicer.md`
- Modify: `version.inc` only if the fork's package version must distinguish the patched host release

**Interfaces:**
- CI jobs: patch check, host tests, plugin tests/package, Linux build check, Windows x64 patched package, and upstream-main compatibility check where supported.
- Release output: source patch, compatibility manifest/schema, checksums, plugin packages, and available patched OrcaSlicer packages.

- [ ] **Step 1: Add patch/application and plugin test workflow**

Checkout exact v2.5.0 base in a clean directory, verify/apply patch, run host tests, run both plugin suites, build packages, validate manifest, and upload artifacts. Pin action major versions consistent with repository conventions.

- [ ] **Step 2: Add upstream-main early-warning job**

Apply or cherry-pick the generic API adaptation to current upstream main in CI. Mark conflicts/test failures as visible failures; do not mutate the stable patch.

- [ ] **Step 3: Adapt Windows installer workflow**

Remove native AI release claims. Release notes identify “OrcaSlicer v2.5.0 + generic Plugin Host API patch,” list independent plugin downloads, and link compatibility manifest. Guard release updates by exact source/patch revision so reruns cannot silently replace a different build.

- [ ] **Step 4: Add platform status generation**

CI updates a generated artifact manifest for its own run; source-controlled compatibility metadata records the last verified statuses. Missing macOS runner remains `not-tested` until actually run.

- [ ] **Step 5: Validate workflow syntax and local commands**

Use available YAML/action linting, run every local script/test, and inspect artifact names for collisions. Triggering/publishing a GitHub release is outward-facing; do it only at the final authorized release step, not during plan implementation tests.

- [ ] **Step 6: Request C++/Python/security review as applicable**

Use `cpp-reviewer` for patch-related C++ changes, `python-reviewer` for scripts, and `security-reviewer` for release/verifier workflows. Fix confirmed findings.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/plugin_host_api.yml \
  .github/workflows/build_windows_installer.yml docs/release/patched-orcaslicer.md version.inc
git commit -m "Build patched host and plugin release artifacts

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### Task 5: Adapt Generic API to Current Upstream Main

**Files:**
- Generic paths only from the Plugin Host API plan, adjusted in an isolated upstream-main worktree.
- No plugin, fork workflow, native AI, profile, or localization files.

**Interfaces:**
- Produces a clean upstream branch with five reviewable commit groups: discovery; storage; snapshot/revision; transaction/selection/rollback; docs/examples.

- [ ] **Step 1: Create isolated worktree from recorded official-main SHA**

Use `superpowers:using-git-worktrees`; update to current official main immediately before final adaptation and record any changed SHA.

- [ ] **Step 2: Replay generic commits in reviewable order**

Resolve upstream architecture differences without squashing unrelated concerns. Preserve tests with each functional commit where practical.

- [ ] **Step 3: Run focused and full available upstream checks**

Build host/tests on the current platform, run slic3rutils tests, stub generation, docs/example smoke checks, and `git diff --check`.

- [ ] **Step 4: Run the automated scope deny scan**

Run `check_plugin_host_commit_scope.py <official-main> <upstream-branch>`. Expected: PASS and no provider/plugin-specific files or terms.

- [ ] **Step 5: Request mandatory fresh reviews**

Use `cpp-reviewer` on the full upstream diff and `security-reviewer` on storage/transaction boundaries. Apply confirmed findings to both upstream branch and stable adaptation where relevant, rerunning both suites.

- [ ] **Step 6: Verify commit history and patch correspondence**

Document intentional differences between current-main implementation and v2.5.0 patch. Ensure API identifiers and Python contract are identical.

### Task 6: Create and Submit the Official Pull Request

**Files:**
- Create locally/temporarily: PR body text (do not commit unless repository convention requires it)
- No new product code unless review finds a required fix

**Interfaces:**
- Produces one official OrcaSlicer pull request containing generic API only.

- [ ] **Step 1: Draft PR title and body**

Title: `Add versioned plugin API for safe preset workflows` (adjust only for maintainer convention).

Body includes problem, generic API surface, user-confirmed atomicity/security, compatibility, tests/platform status, and two motivating plugin classes: AI settings assistant and filament inventory/QR slot selection. It explicitly says neither plugin/provider is included. End with required attribution:

```text
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

- [ ] **Step 2: Run final verification before any push**

From the upstream branch run status, log/range inspection, scope checker, focused/full available tests, and confirm no secrets/untracked artifacts. From the release branch rerun patch verifier and plugin compatibility validator.

- [ ] **Step 3: Push the upstream branch**

This is outward-facing but explicitly requested by the user after completion. Push to the user's authorized fork remote; do not force-push or overwrite an unrelated branch.

- [ ] **Step 4: Create the PR with `gh pr create`**

Target official OrcaSlicer `main`, include the verified body, and capture the returned URL. If authentication is required, ask the user to run `! gh auth login`; never expose tokens.

- [ ] **Step 5: Verify the published PR**

Use `gh pr view` to confirm base/head, title/body, changed files, commits, and CI state. If files outside allowed generic scope appear, close/fix only with explicit care; never claim success before correcting the PR.

- [ ] **Step 6: Report delivery**

Give the user the PR URL, exact tested commit SHA, stable patch path/hash, plugin artifact versions, platform status, and any honest remaining limitations. Do not call any component final if a release gate failed.
