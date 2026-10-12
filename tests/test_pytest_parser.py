from test_repair.pytest_parser import compact_failures, parse_pytest_output

ASSERTION_OUTPUT = """\
FFF...                                                                   [100%]
=================================== FAILURES ===================================
_______________________________ test_first_page ________________________________
tests/test_pager.py:9: in test_first_page
    assert page(ITEMS, 1, 3) == [1, 2, 3]
E   assert [4, 5, 6] == [1, 2, 3]
E     At index 0 diff: 4 != 1
_______________________________ test_middle_page _______________________________
tests/test_pager.py:13: in test_middle_page
    assert page(ITEMS, 2, 3) == [4, 5, 6]
E   assert [7, 8, 9] == [4, 5, 6]
____________________________ TestPages.test_last[4] ____________________________
tests/test_pager.py:30: in test_last
    assert last(n) == 10
pager/core.py:22: in last
    return helper(n)
pager/util.py:3: in helper
    raise IndexError("out of range")
E   IndexError: out of range
=========================== short test summary info ============================
FAILED tests/test_pager.py::test_first_page - assert [4, 5, 6] == [1, 2, 3]
FAILED tests/test_pager.py::test_middle_page - assert [7, 8, 9] == [4, 5, 6]
FAILED tests/test_pager.py::TestPages::test_last[4] - IndexError: out of range
3 failed, 3 passed, 1 warning in 0.10s
"""

COLLECTION_ERROR_WINDOWS = r"""
==================================== ERRORS ====================================
____________________ ERROR collecting tests/test_shapes.py _____________________
ImportError while importing test module 'C:\tmp\w\tests\test_shapes.py'.
Traceback:
C:\Python\Lib\importlib\__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests\test_shapes.py:5: in <module>
    from shapes import circle_area, rectangle_area
shapes\area.py:5: in <module>
    from .units import to_square_meters
E   ImportError: cannot import name 'to_square_meters' from 'shapes.units'
=========================== short test summary info ============================
ERROR tests\test_shapes.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.20s
"""


def test_assertion_failures_are_structured():
    report = parse_pytest_output(ASSERTION_OUTPUT, exit_code=1)
    assert (report.passed, report.failed, report.errors) == (3, 3, 0)
    assert report.failing_ids() == [
        "tests/test_pager.py::test_first_page",
        "tests/test_pager.py::test_middle_page",
        "tests/test_pager.py::TestPages::test_last[4]",
    ]
    first = report.failures[0]
    assert first.message == "assert [4, 5, 6] == [1, 2, 3]"
    assert first.frames[0].path == "tests/test_pager.py" and first.frames[0].line == 9
    nested = report.failures[2]
    assert [f.path for f in nested.frames] == ["tests/test_pager.py", "pager/core.py", "pager/util.py"]
    assert nested.frames[-1].func == "helper"
    assert not report.all_passed


def test_collection_error_with_windows_paths():
    report = parse_pytest_output(COLLECTION_ERROR_WINDOWS, exit_code=2)
    assert report.errors == 1 and report.passed == 0
    (failure,) = report.failures
    assert failure.kind == "error"
    assert failure.test_id == "tests/test_shapes.py"
    assert "ImportError" in failure.message
    paths = [f.path for f in failure.frames]
    assert "shapes/area.py" in paths
    assert paths[0].startswith("C:/Python")


def test_all_passed():
    report = parse_pytest_output("....                [100%]\n4 passed in 0.01s\n", exit_code=0)
    assert report.all_passed and report.passed == 4 and report.failures == []


def test_truncated_output_falls_back_to_sections():
    truncated = ASSERTION_OUTPUT.split("=========================== short")[0]
    report = parse_pytest_output(truncated, exit_code=1)
    assert report.failed == 3
    assert report.failures[0].test_id == "test_first_page"


def test_timeout_report():
    report = parse_pytest_output("..", exit_code=-9, timed_out=True)
    assert report.timed_out and not report.all_passed
    assert "TIMED OUT" in compact_failures(report)


def test_compact_failures_is_short():
    report = parse_pytest_output(ASSERTION_OUTPUT, exit_code=1)
    text = compact_failures(report)
    assert "3 passed, 3 failed" in text
    assert "pager/util.py:3 in helper" in text
    assert len(text) < len(ASSERTION_OUTPUT)


def test_signature_ignores_order():
    a = parse_pytest_output(ASSERTION_OUTPUT, exit_code=1)
    b = parse_pytest_output(ASSERTION_OUTPUT, exit_code=1)
    b.failures.reverse()
    assert a.signature() == b.signature()
