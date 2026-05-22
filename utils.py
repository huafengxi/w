import urllib.parse


def parse_qs_to_dict(qs):
    return {k: v[-1] for k, v in urllib.parse.parse_qs(qs).items()}


def safe_read_text(p):
    with open(p) as f:
        return f.read()
