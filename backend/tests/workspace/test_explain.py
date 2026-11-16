import pytest

from odn.platform.workspace.explain import explain_artifact, explain_chart, explain_lineage, explain_sql


@pytest.mark.parametrize(
    ("sql", "dialect", "inputs", "expected"),
    [
        (
            "SELECT addr_state, COUNT(*) AS n FROM `p.d.stg_staging_out` GROUP BY addr_state",
            "bigquery", ["stg_staging_out"],
            "Counted rows from stg_staging_out, per addr_state.",
        ),
        (
            "SELECT addr_state, business_status, COUNT(1) AS n FROM t GROUP BY 1, 2",
            "bigquery", ["stg_staging_out"],
            "Counted rows from stg_staging_out, per 1 and 2.",
        ),
        (
            (
                "SELECT addr_state, SUM(n) AS total FROM q1 WHERE addr_state IS NOT NULL GROUP BY ALL "
                "ORDER BY total DESC LIMIT 10"
            ),
            "duckdb", ["q1"],
            (
                "Summed n from q1, per addr_state, where excluding missing addr_state, "
                "keeping the top 10 by total (highest first)."
            ),
        ),
        (
            "SELECT * FROM q1 WHERE business_status = 'A' AND n > 100",
            "duckdb", ["q1"],
            "Selected all columns from q1, where business_status = 'A' and n > 100.",
        ),
        (
            "SELECT addr_state, total, RANK() OVER (ORDER BY total DESC) AS rnk FROM q3",
            "duckdb", ["q3"],
            "Selected addr_state, total and rnk from q3, with rank calculated across rows.",
        ),
        (
            "SELECT COUNT(DISTINCT addr_state) AS states, AVG(n) AS avg_n FROM q1",
            "duckdb", ["q1"],
            "Counted distinct addr_state and averaged n from q1.",
        ),
        (
            "WITH s AS (SELECT addr_state, SUM(n) AS t FROM q1 GROUP BY 1) SELECT * FROM s ORDER BY t LIMIT 5",
            "duckdb", ["q1"],
            "Summed n from q1, using intermediate steps, keeping the top 5 by t (lowest first).",
        ),
        ("SELECT a FROM q1 UNION ALL SELECT a FROM q2", "duckdb", ["q1", "q2"],
         "Combined the results of several queries over q1 and q2 (see SQL)."),
        (
            ("SELECT s.addr_state, COUNT(f.clip_id) AS n FROM `p.d.firmo` f JOIN `p.d.addr` s ON f.id = s.id "
            "GROUP BY 1"),
            "bigquery", ["firmo", "addr"],
            "Counted clip_id from firmo joined with addr, per 1.",
        ),
        ("SELEC broken", "duckdb", ["q1"], "Computed a result from q1 (see SQL)."),
        ("SELECT FROM WHERE (", "duckdb", ["q1"],
         "Computed a result from q1; the logic could not be summarized automatically (see SQL)."),
    ],
)
def test_explain_sql(sql: str, dialect: str, inputs: list[str], expected: str) -> None:
    assert explain_sql(sql, dialect, inputs) == expected


def test_warehouse_lineage_mentions_scan_and_partial() -> None:
    text = explain_lineage(
        kind="warehouse", source_tables=["p.d.stg_staging_out"], derived_from=[], truncated=True,
        row_count=500_000, bytes_processed=655_718_648,
    )
    assert text == "Read directly from stg_staging_out in the warehouse (0.61 GB scanned). Partial result: capped at 500,000 rows."


def test_workspace_lineage_states_no_rescan_and_unverified_source() -> None:
    assert explain_lineage(kind="workspace", source_tables=[], derived_from=["q1"], truncated=False, row_count=3) == (
        "Derived from q1 in this conversation without a new warehouse scan; original source could not be verified."
    )


def test_artifact_explanation_is_two_sentences() -> None:
    text = explain_artifact(
        kind="workspace", sql="SELECT addr_state, SUM(n) AS total FROM q1 GROUP BY ALL", source_tables=["p.d.t"],
        derived_from=["q1"], truncated=False, row_count=54,
    )
    assert text == (
        "Summed n from q1, per addr_state. "
        "Derived from q1 in this conversation without a new warehouse scan; originally from t."
    )


def test_chart_explanation() -> None:
    assert explain_chart(
        chart_type="bar", x="addr_state", y="business_count", agg="sum", series=None, sort="y_desc", limit=10,
        points=10, truncated=True, source="q2", source_truncated=False,
    ) == "Bar chart of sum of business_count by addr_state, from q2; the 10 highest values are shown."
