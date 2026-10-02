# 修改记录:
#   2026-10-02  Claude  新建: conftest 的 _redirect_csv_output 确实把各 CSV 输出口指向 tmp_path
"""测试期 CSV 输出重定向(tests/conftest.py::_redirect_csv_output)的正反例"""
from pathlib import Path

PROJECT_CSV = Path(__file__).resolve().parents[2] / "csv"


def test_checker_csv_goes_to_tmp(tmp_path):
    """正例: util.checker 写出的差异 CSV 落在 tmp_path/csv"""
    from util import checker
    path = checker.write_diff_csv("redirect_probe", "2000-01-01", "2000-01-01",
                                  ["a"], [("1",)])
    assert Path(path).parent == tmp_path / "csv"
    assert Path(path).exists()


def test_check_daily_and_adjust_csv_dirs_point_to_tmp(tmp_path):
    """正例: check_daily / check_adjust 按 __file__ 现算的 csv 目录指向 tmp_path/csv"""
    import tools.check_adjust
    import tools.check_daily
    assert Path(tools.check_daily.__file__).parent.parent / "csv" == tmp_path / "csv"
    assert tools.check_adjust._csv_path("x", "a", "b").parent == tmp_path / "csv"


def test_import_daily_export_dir_points_to_tmp(tmp_path):
    """正例: import_daily -p 的导出目录指向 tmp_path/csv"""
    import etl.import_daily
    assert etl.import_daily.CSV_DIR == tmp_path / "csv"


def test_no_output_dir_points_to_project_csv():
    """反例(关键): 任何输出口都不得再指向项目 csv/"""
    import etl.import_daily
    import tools.check_adjust
    import tools.check_daily
    from util import checker
    dirs = [checker.CSV_DIR, etl.import_daily.CSV_DIR,
            Path(tools.check_daily.__file__).parent.parent / "csv",
            tools.check_adjust._csv_path("x", "a", "b").parent]
    assert all(Path(d).resolve() != PROJECT_CSV.resolve() for d in dirs)
