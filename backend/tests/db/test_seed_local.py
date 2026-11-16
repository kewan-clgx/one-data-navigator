import pytest

from odn.db.migrate import local_bigquery_allowlist

P = "clgx-idap-bigquery-int-278e"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "a, b ,a"}, (P, [f"{P}.a.*", f"{P}.b.*"], "a")),
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "(\na,\nb,\n)"}, (P, [f"{P}.a.*", f"{P}.b.*"], "a")),
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "(\na,\n#b,\nc, # note\n)"}, (P, [f"{P}.a.*", f"{P}.c.*"], "a")),
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "*"}, (P, [f"{P}.*.*"], None)),
        ({"BQ_PROJECT_ID": P, "BQ_TABLES": "(\nd1.vw_a,\nd1.t-b\n)"}, (P, [f"{P}.d1.vw_a", f"{P}.d1.t-b"], "d1")),
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "a, b", "BQ_TABLES": f"a.t1, {P}.a.t2, c.t"},
         (P, [f"{P}.b.*", f"{P}.a.t1", f"{P}.a.t2", f"{P}.c.t"], "a")),
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_ID": "x", "BQ_DATASET_ID_2": "y"}, (P, [f"{P}.x.*", f"{P}.y.*"], "x")),
        ({"BQ_PROJECT_ID": P}, (P, [f"{P}.*.*"], None)),
        ({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "", "BQ_DATASET_ID": " "}, (P, [f"{P}.*.*"], None)),
        ({"BQ_DATASET_IDS": "a"}, None),
    ],
)
def test_allowlist_from_env(env: dict[str, str], expected: object) -> None:
    assert local_bigquery_allowlist(env) == expected


def test_rejects_injected_dataset_ids() -> None:
    with pytest.raises(SystemExit):
        local_bigquery_allowlist({"BQ_PROJECT_ID": P, "BQ_DATASET_IDS": "ok,bad.name"})


@pytest.mark.parametrize("entry", ["just_table", "other-project.d.t", "d.t.x.y", "d.t;drop", "d.t`"])
def test_rejects_bad_table_entries(entry: str) -> None:
    with pytest.raises(SystemExit):
        local_bigquery_allowlist({"BQ_PROJECT_ID": P, "BQ_TABLES": entry})
