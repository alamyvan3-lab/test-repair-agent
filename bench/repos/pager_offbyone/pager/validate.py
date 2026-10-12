def check_page_args(number: int, size: int) -> None:
    if size < 1:
        raise ValueError("size must be >= 1")
    if number < 1:
        raise ValueError("page numbers start at 1")
