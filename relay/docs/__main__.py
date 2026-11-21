"""uv run python -m relay.docs friday — print the German task order for the Friday 'sent' option."""
import json
import sys

from relay.config import REFERENCE
from relay.docs import friday_sent_option, task_order_de


def main(argv: list[str]) -> int:
    if argv != ["friday"]:
        print("usage: python -m relay.docs friday", file=sys.stderr)
        return 2
    scenario = json.loads((REFERENCE / "scenarios" / "friday.json").read_text(encoding="utf-8"))
    print(task_order_de(friday_sent_option(scenario), scenario), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
