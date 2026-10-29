

def test_stats_publishes_a_median_and_a_spread():
    """`stats` answered with min/max/avg/sum, and 393 of PHIA's 4,000 objective
    questions ask for a median (281 for a standard deviation). Asked for the
    median deep-sleep percentage, a local 2B answered with one day's value —
    it had no statistic to read, only rows to count by hand.
    """
    from mirobody.collect import query as collect_query

    source = collect_query._STATS_CTE  # noqa: F841  (documented here, asserted below)
    sql = str(collect_query._STAT_MEDIAN) + str(collect_query._STAT_STDDEV)
    assert "PERCENTILE_CONT(0.5)" in sql
    assert "STDDEV_SAMP" in sql


def test_the_median_respects_the_unit_guard():
    """A median across mg/dL and mmol/L is not a median of anything."""
    from mirobody.collect import query as collect_query

    for macro in (collect_query._STAT_MEDIAN, collect_query._STAT_STDDEV):
        assert "COUNT(DISTINCT unit_ucum) <= 1" in str(macro)
