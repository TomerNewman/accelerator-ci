from pathlib import Path


def load_template(filename: str, templates_dir: str) -> str:
    return Path(templates_dir, filename).read_text()
