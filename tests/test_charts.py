"""图表几何：缺失断开、区间不画均值线、空序列不画坐标系。"""

from __future__ import annotations

from health_agent.ui.charts import BandPoint, SeriesPoint, build_band, build_bars, build_line


def _pts(*values):
    return [SeriesPoint(label=f"10-{i + 1:02d}", value=v) for i, v in enumerate(values)]


def test_line_breaks_at_missing_day():
    """缺失处在折线中断开：两段独立折线，中间一天不连线。"""
    model = build_line(_pts(400, 410, None, 420, 430))

    assert model["has_data"] is True
    assert len(model["segments"]) == 2
    assert len(model["missing"]) == 1
    assert model["missing"][0]["label"] == "10-03"
    assert len(model["dots"]) == 4


def test_line_without_missing_is_single_segment():
    model = build_line(_pts(400, 410, 420))
    assert len(model["segments"]) == 1
    assert model["missing"] == []


def test_all_missing_has_no_data():
    model = build_line(_pts(None, None, None))

    assert model["has_data"] is False
    assert model["segments"] == []
    assert model["y_ticks"] == []


def test_single_point_is_dot_without_segment():
    model = build_line(_pts(None, 410, None))

    assert model["segments"] == []
    assert len(model["dots"]) == 1
    assert model["dots"][0]["value"] == 410


def test_flat_series_does_not_divide_by_zero():
    model = build_line(_pts(400, 400, 400))
    assert model["has_data"] is True
    assert all(isinstance(tick["y"], float) for tick in model["y_ticks"])


def test_bars_mark_missing_as_hollow():
    model = build_bars(_pts(400, None, 420))

    assert model["has_data"] is True
    assert model["bars"][1]["missing"] is True
    assert model["bars"][0]["missing"] is False


def test_bars_include_target_line_when_given():
    model = build_bars(_pts(400, 410), target=420)

    assert model["target"] is not None
    assert model["target"]["label"] == "420"


def test_band_requires_both_ends():
    points = [
        BandPoint("10-01", 47, 82),
        BandPoint("10-02", 50, None),
        BandPoint("10-03", 45, 79),
    ]
    model = build_band(points)

    assert model["has_data"] is True
    assert model["area"].startswith("M")
    assert model["area"].count("L") >= 3
    # 只有成对的上下限才进带子：3 天里第 2 天缺上限，不应被补一个值
    assert model["low_line"].count(" ") == 1


def test_band_without_pairs_has_no_data():
    model = build_band([BandPoint("10-01", None, 82), BandPoint("10-02", 50, None)])
    assert model["has_data"] is False
    assert model["area"] == ""


def test_band_has_no_center_line():
    """区间图只有上下两条边，没有中间线——中间线等于伪造平均值。"""
    model = build_band([BandPoint("10-01", 47, 82), BandPoint("10-02", 50, 80)])
    assert set(model) >= {"area", "low_line", "high_line"}
    assert "mean_line" not in model
    assert "mid_line" not in model
