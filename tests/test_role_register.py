"""ADR-0043 — the seven-row register the /work second screen prints.

RED-first: every case below describes the payload before the reader exists.
`_parse_ownership` walked `cells[0..2]` and threw the fourth column away, so
`บันทึกผลลงที่` had no reader at all and nothing could answer "does the place
this role records into exist yet".

The fixture writes a miniature workspace rather than pointing at the real one,
for the reason `test_pipeline_surfaces.py` gives: the whole point is that the
register is *not* pinned in code, and a test asserting against today's real
roles.md would pass for the wrong reason the day a role moves office.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from control_plane import workspace  # noqa: E402


ROLES = """# roles

## แกนความเป็นเจ้าของ — role → office · discipline · ที่บันทึก

| role | office | เปิด discipline leaf ใน `team/` | บันทึกผลลงที่ |
| --- | :-: | --- | --- |
| `cto` | `build` | `arch` · `security` | `meta/adr-*.md` |
| `tech-lead` | `build` | `software-design` · `ux-ui` | `projects/<n>/docs/adr/` + living HLD `docs/design/*` |
| `senior-developer` | `build` | `dev` · `devex` | commit body · ADR เมื่อการตัดสินผูกทั้งระบบ |
| `developer` | `build` | `dev` | commit body |
| `devops` | `run` | `infra` | runbook (`docs/runbooks/` · platform-core) |
| `qa` | `run` | — (`../ways-of-working/code-review.md` · `definition-of-done.md`) | PR review thread + DoD ในตัว PR |
| `product-owner` | `business` | `forge` · `ops` | `projects/*/slices.md` · OpenProject |

## โมเดลต่อ role

| Role | default | effort |
| --- | :-: | :-: |
| **CTO** | **heavy** | `xhigh` |
"""


def _workspace(tmp_path: Path, roles: str = ROLES) -> Path:
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    (tmp_path / "team-os" / "people" / "roles.md").write_text(roles, encoding="utf-8")

    # Workspace-level surfaces.
    (tmp_path / "meta").mkdir()
    for n in ("one", "two", "three"):
        (tmp_path / "meta" / f"adr-{n}.md").write_text("x", encoding="utf-8")
    # The collision §SD3 exists for: `docs/runbooks/` is real at BOTH levels.
    (tmp_path / "docs" / "runbooks").mkdir(parents=True)

    for name in ("alpha", "beta"):
        (tmp_path / "projects" / name / "docs" / "design").mkdir(parents=True)
        (tmp_path / "projects" / name / "slices.md").write_text("x", encoding="utf-8")
        (tmp_path / "projects" / name / "docs" / "design" / "hld.md").write_text(
            "x", encoding="utf-8"
        )
    (tmp_path / "projects" / "alpha" / "docs" / "adr").mkdir()
    (tmp_path / "projects" / "alpha" / "docs" / "runbooks").mkdir()
    return tmp_path


def _row(register: dict, role: str) -> dict:
    return next(r for r in register["roles"] if r["role"] == role)


def test_every_role_carries_every_column_of_its_row(tmp_path):
    reg = workspace.role_register(_workspace(tmp_path))

    assert reg["present"] is True
    assert [r["role"] for r in reg["roles"]] == [
        "cto",
        "tech-lead",
        "senior-developer",
        "developer",
        "devops",
        "qa",
        "product-owner",
    ]
    # Row order is § แกนความเป็นเจ้าของ's own, not § โมเดลต่อ role's — the two
    # tables really are ordered differently in the workspace (§SD7).
    cto = _row(reg, "cto")
    assert cto["office"] == "build"
    assert cto["disciplines"] == ["arch", "security"]
    assert cto["records"]["raw"] == "`meta/adr-*.md`"


def test_the_fourth_column_now_has_a_reader(tmp_path):
    reg = workspace.role_register(_workspace(tmp_path))
    # The regression this whole slice exists for: before ADR-0043 the keys per
    # role were exactly {office, disciplines} and nothing downstream could ask.
    assert set(_row(reg, "cto")) == {
        "role",
        "office",
        "disciplines",
        "disciplines_raw",
        "disciplines_dropped",
        "records",
    }


def test_a_cell_with_no_path_token_says_not_files_never_an_empty_list(tmp_path):
    """§SD4 — the distinction a blank cell would destroy."""
    reg = workspace.role_register(_workspace(tmp_path))

    for role in ("senior-developer", "developer", "qa"):
        records = _row(reg, role)["records"]
        assert records["kind"] == "not-files"
        assert records["targets"] == []
        # The prose survives: it is the only thing that tells senior-developer's
        # line apart from developer's.
        assert records["note"]

    assert "ADR" in _row(reg, "senior-developer")["records"]["note"]
    for role in ("cto", "tech-lead", "devops", "product-owner"):
        assert _row(reg, role)["records"]["kind"] == "files"


def test_a_placeholder_becomes_a_glob_and_says_so(tmp_path):
    """§SD3 — `<n>` is the workspace's own way of writing "any project"."""
    targets = _row(workspace.role_register(_workspace(tmp_path)), "tech-lead")[
        "records"
    ]["targets"]
    adr = next(t for t in targets if "<n>" in t["token"])

    assert adr["glob"] == "projects/*/docs/adr"
    assert adr["levels"]["workspace"]["have"] == 1
    assert adr["levels"]["workspace"]["paths"] == ["projects/alpha/docs/adr"]


def test_both_levels_are_always_resolved(tmp_path):
    """§SD3 — where this reader parts company with `_count_target`.

    `docs/runbooks/` is real at the workspace root AND under a project. Trying
    the root first and stopping would delete half the answer with nothing on
    screen saying it had been deleted.
    """
    target = _row(workspace.role_register(_workspace(tmp_path)), "devops")["records"][
        "targets"
    ][0]

    assert target["levels"]["workspace"]["paths"] == ["docs/runbooks"]
    assert target["levels"]["project"]["paths"] == ["projects/alpha/docs/runbooks"]
    assert target["levels"]["project"]["projects"] == 1


def test_a_project_relative_token_still_resolves_below_projects(tmp_path):
    targets = _row(workspace.role_register(_workspace(tmp_path)), "tech-lead")[
        "records"
    ]["targets"]
    design = next(t for t in targets if t["token"] == "docs/design/*")

    assert design["levels"]["workspace"]["have"] == 0
    assert design["levels"]["project"]["have"] == 2
    assert design["levels"]["project"]["projects"] == 2


def test_the_project_level_splits_per_project(tmp_path):
    """ADR-0043 Amendment (S34) — the picker needs an exact per-project count.

    Not derivable in the browser: `paths` is a capped sample, so filtering it
    would report zero for a project whose matches fell off the end.
    """
    targets = _row(workspace.role_register(_workspace(tmp_path)), "tech-lead")[
        "records"
    ]["targets"]
    design = next(t for t in targets if t["token"] == "docs/design/*")
    by_project = design["levels"]["project"]["by_project"]

    assert [p["project"] for p in by_project] == ["alpha", "beta"]
    assert all(p["have"] == 1 for p in by_project)
    assert by_project[0]["paths"] == ["projects/alpha/docs/design/hld.md"]
    # The aggregate stays beside it — the register is workspace-level, and a
    # filtered column must never look like the whole answer.
    assert design["levels"]["project"]["have"] == 2
    assert design["levels"]["project"]["projects"] == 2


def test_a_project_with_no_match_is_absent_from_the_split_not_zero_filled(tmp_path):
    """`gamma` carries nothing, so it has no row — the browser reads absence as
    a real zero rather than the payload carrying 33 empty entries."""
    ws = _workspace(tmp_path)
    (ws / "projects" / "gamma").mkdir()

    targets = _row(workspace.role_register(ws), "tech-lead")["records"]["targets"]
    design = next(t for t in targets if t["token"] == "docs/design/*")

    assert "gamma" not in [p["project"] for p in design["levels"]["project"]["by_project"]]


def test_a_pattern_that_matches_nothing_is_zero_not_missing(tmp_path):
    ws = _workspace(tmp_path)
    (ws / "meta" / "adr-one.md").unlink()
    (ws / "meta" / "adr-two.md").unlink()
    (ws / "meta" / "adr-three.md").unlink()

    target = _row(workspace.role_register(ws), "cto")["records"]["targets"][0]
    assert target["levels"]["workspace"] == {"have": 0, "paths": [], "more": 0}
    assert _row(workspace.role_register(ws), "cto")["records"]["kind"] == "files"


def test_the_path_list_is_capped_and_says_how_many_more(tmp_path):
    ws = _workspace(tmp_path)
    for n in range(10):
        (ws / "meta" / f"adr-extra-{n}.md").write_text("x", encoding="utf-8")

    level = _row(workspace.role_register(ws), "cto")["records"]["targets"][0]["levels"][
        "workspace"
    ]
    assert level["have"] == 13
    assert len(level["paths"]) == workspace._PATH_SAMPLE
    assert level["more"] == 13 - workspace._PATH_SAMPLE


def test_a_token_that_walks_out_of_the_workspace_is_reported_not_dropped(tmp_path):
    roles = ROLES.replace("`meta/adr-*.md`", "`../../etc/*`")
    records = _row(workspace.role_register(_workspace(tmp_path, roles)), "cto")[
        "records"
    ]

    assert records["rejected"] == ["../../etc/*"]
    assert records["targets"] == []


def test_qa_holds_no_discipline_and_the_stray_text_is_reported(tmp_path):
    """§SD5 — the cell says `—`; the two file names after it are a pointer."""
    qa = _row(workspace.role_register(_workspace(tmp_path)), "qa")

    assert qa["disciplines"] == []
    assert len(qa["disciplines_dropped"]) == 2
    assert all(
        "code-review" in d or "definition-of-done" in d for d in qa["disciplines_dropped"]
    )
    # Nothing disappears: the raw cell travels so a wrong filter is visible.
    assert "code-review.md" in qa["disciplines_raw"]


def test_the_discipline_filter_does_not_move_the_belt(tmp_path):
    """The two stray strings never mapped a stage, so ADR-0041's panel is
    byte-identical before and after §SD5 — asserted rather than assumed."""
    ws = _workspace(tmp_path)
    ownership = workspace._parse_ownership(ws / "team-os" / "people" / "roles.md")

    assert ownership["qa"]["disciplines"] == []
    assert ownership["tech-lead"]["disciplines"] == ["software-design", "ux-ui"]


def test_heavy_when_is_declared_unread_and_never_parsed(tmp_path):
    """§SD6 — S19's column. The board must say it cannot read it, not guess."""
    reg = workspace.role_register(_workspace(tmp_path))

    assert reg["heavy_when"]["readable"] is False
    assert reg["heavy_when"]["slice"] == "S19"
    assert reg["heavy_when"]["reason"]
    # No role row carries anything resembling an escalation condition.
    assert all("heavy_when" not in r for r in reg["roles"])


def test_an_unreadable_register_says_so_instead_of_printing_no_roles(tmp_path):
    (tmp_path / "team-os" / "people").mkdir(parents=True)
    reg = workspace.role_register(tmp_path)

    assert reg["present"] is False
    assert reg["reason"]
    assert reg["roles"] == []


def test_the_overview_carries_the_register(tmp_path):
    ws = _workspace(tmp_path)
    (ws / "docs" / "sops").mkdir(parents=True, exist_ok=True)
    payload = workspace.workspace_overview(str(ws), use_cache=False)

    assert payload["register"]["present"] is True
    assert len(payload["register"]["roles"]) == 7
    assert payload["register"]["source"] == "team-os/people/roles.md"


# ── ADR-0043 Amendment (2) — `kind` moves from the cell to the target (S35/S36) ──

ROLES_MIXED = ROLES.replace(
    "| `tech-lead` | `build` | `software-design` · `ux-ui` | "
    "`projects/<n>/docs/adr/` + living HLD `docs/design/*` |",
    "| `tech-lead` | `build` | `software-design` · `ux-ui` | "
    "`projects/<n>/docs/adr/` + living HLD `docs/design/*` "
    "+ artifact ที่ sign-off แล้ว `surface:pr-body` (URL + version) |",
)


def test_a_mixed_cell_carries_both_kinds_of_target(tmp_path):
    """§A5 — a cell naming both a file and a surface prints both, and the
    cell-level summary becomes `mixed` rather than reporting only one half."""
    records = _row(
        workspace.role_register(_workspace(tmp_path, ROLES_MIXED)), "tech-lead"
    )["records"]

    assert records["kind"] == "mixed"
    kinds = {t["kind"] for t in records["targets"]}
    assert kinds == {"file", "surface"}

    surface = next(t for t in records["targets"] if t["kind"] == "surface")
    assert surface["token"] == "surface:pr-body"
    assert surface["slug"] == "pr-body"
    # A surface is not glob'd or counted (§A8) — it carries no location fields.
    assert "glob" not in surface
    assert "levels" not in surface

    files = [t for t in records["targets"] if t["kind"] == "file"]
    assert {t["token"] for t in files} == {"projects/<n>/docs/adr/", "docs/design/*"}


def test_a_cell_with_only_file_targets_still_summarises_as_files(tmp_path):
    """§A5 — the third value is additive; a cell that never named a surface
    keeps the same summary it always had."""
    reg = workspace.role_register(_workspace(tmp_path))
    for role in ("cto", "tech-lead", "devops", "product-owner"):
        assert _row(reg, role)["records"]["kind"] == "files"


def test_a_token_that_is_neither_a_path_nor_a_surface_survives_in_the_note(tmp_path):
    """§A7 — RED case measured directly against `_resolve_records()` in the
    amendment's own evidence section: a backtick token that is not path-shaped
    and not a `surface:` token used to be stripped from `targets` **and**
    `note` by the old `_CODE_TOKEN.sub("", raw)` blanket wipe."""
    roles = ROLES.replace(
        "`meta/adr-*.md`",
        "`meta/adr-*.md` — ใบ `type: surface` ที่ถือ url:",
    )
    records = _row(workspace.role_register(_workspace(tmp_path, roles)), "cto")[
        "records"
    ]

    # The file target is still resolved — unaffected by the token beside it.
    assert records["kind"] == "files"
    assert [t["token"] for t in records["targets"]] == ["meta/adr-*.md"]
    # The non-target token's text is not thrown away with its backticks.
    assert "type: surface" in records["note"]


def test_a_rejected_token_is_still_cut_from_the_note(tmp_path):
    """§A7 only spares tokens that fell through *both* checks — a token
    rejected for walking outside the workspace is still a target-shaped
    decision, reported via `rejected`, and still cut from the prose."""
    roles = ROLES.replace("`meta/adr-*.md`", "`../../etc/*` — ดูรายละเอียด")
    records = _row(workspace.role_register(_workspace(tmp_path, roles)), "cto")[
        "records"
    ]

    assert records["rejected"] == ["../../etc/*"]
    assert records["targets"] == []
    assert records["kind"] == "not-files"
    assert "../../etc/*" not in records["note"]
    assert "ดูรายละเอียด" in records["note"]


def test_points_at_nothing_filters_by_target_kind_not_cell_kind(tmp_path):
    """§A5 — `pointsAtNothing`'s Python-side twin does not exist (the check is
    TS-only, `role-register.ts`), but the payload it filters on is built here:
    a mixed cell must carry each target's own `kind` so that filter works."""
    records = _row(
        workspace.role_register(_workspace(tmp_path, ROLES_MIXED)), "tech-lead"
    )["records"]
    file_targets = [t for t in records["targets"] if t["kind"] == "file"]
    assert len(file_targets) == 2
    assert all("levels" in t for t in file_targets)
