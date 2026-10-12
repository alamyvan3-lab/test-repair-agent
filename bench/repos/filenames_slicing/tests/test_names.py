from files import extension, stem


def test_extension():
    assert extension("report.PDF") == "pdf"
    assert extension("archive.tar.gz") == "gz"
    assert extension("dir.v2/notes.txt") == "txt"


def test_no_extension():
    assert extension("README") == ""
    assert extension(".bashrc") == ""


def test_stem():
    assert stem("dir/report.pdf") == "report"
    assert stem(".bashrc") == ".bashrc"
