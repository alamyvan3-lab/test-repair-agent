from .parse import DEFAULTS, parse_int


def load(lines: list[str]) -> dict:
    values = dict(line.split("=", 1) for line in lines if "=" in line)
    return {
        "port": parse_int(values.get("port"), DEFAULTS["port"]),
        "workers": parse_int(values.get("workers"), DEFAULTS["workers"]),
        "debug": values.get("debug", "false").strip().lower() == "true",
    }
