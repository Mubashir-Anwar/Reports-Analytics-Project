"""Container liveness check: exit 0 when the service answers HTTP at all.

/health answers 503 while MongoDB is unreachable; that is a database problem, not a dead process, so a
restart would not help. Readiness (is MongoDB reachable?) is the JSON of /health itself.
"""
import sys
import urllib.error
import urllib.request


def main() -> int:
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=4)
    except urllib.error.HTTPError:
        return 0  # the process answered (e.g. 503 = database down)
    except OSError:
        return 1  # no answer: the process is stuck or not listening
    return 0


if __name__ == "__main__":
    sys.exit(main())
