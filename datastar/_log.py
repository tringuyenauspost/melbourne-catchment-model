"""One logger for the whole build, and the primitives a phase report is written with.

Every `print` in `pipeline/` is a `log.info` now. The reason is not tidiness: the build runs as
seven separate processes under `run_pipeline.py`, and when its output is redirected to a file the
runner's own banners are block-buffered while the children's are not, so the transcript comes out
in the WRONG ORDER — the 438-line baseline had every step's output above the banner that said
which step was running. `logging.StreamHandler` flushes on every record, so the transcript is in
the order it happened whether you watch it or redirect it. That alone was worth the change.

What it buys beyond that: a level (`MELB_LOG_LEVEL=WARNING` for a quiet build), a timestamped
format for when you need to know how long a phase took (`MELB_LOG_STYLE=stamped`), and a copy on
disk (`MELB_LOG_FILE=build.log`) that the stamped format is always used for — the console keeps
the plain shape the messages were written in, the file gets the metadata.

    MELB_LOG_STYLE=plain|stamped     console format      (default plain — `%(message)s`)
    MELB_LOG_LEVEL=DEBUG|INFO|...    console threshold   (default INFO)
    MELB_LOG_FILE=path               also append there, always stamped, always DEBUG

WHY A NAMED LOGGER AND NOT `basicConfig`. The reduction in `s1a_export_chain2_factors.py` is a
LIBRARY: eleven scripts outside the build import it, and a module that reconfigures the root
logger on import would hijack the logging of every one of them. Everything here hangs off a
single `melb` logger with `propagate = False`, so the build's output is the build's own and
nothing else's handlers see it.

THE PRIMITIVES. `banner`, `kv`, `dist` and `table` exist so a phase report is a list of facts
rather than a wall of f-strings. `dist` in particular is the shape the scan Sankeys print — a
bucket, its volume, its share, and the running cumulative — because that table is the one the
manager reads, and there should be exactly one implementation of it.

    from _log import get_logger, banner, dist
    log = get_logger(__file__)
"""
import logging
import os
import sys
from pathlib import Path

ROOT = "melb"                    # every logger in the build hangs off this one
PLAIN = "%(message)s"            # the messages carry their own indentation — do not add to it
STAMPED = "%(asctime)s  %(levelname)-5s  %(name)-6s  %(message)s"

_ready = False


def _step_name(who):
    """`.../s1a_export_chain2_factors.py` -> `s1a`; anything else -> its own short name.

    The step prefix is the whole identity of a pipeline file, so in `stamped` mode the name
    column is three characters wide and lines up. A caller that is not a step (a helper, a
    notebook) keeps whatever it passed.
    """
    name = Path(str(who)).stem if str(who).endswith(".py") else str(who)
    head = name.split("_", 1)[0]
    return head if len(head) == 3 and head[0] == "s" and head[1].isdigit() else name


def _configure():
    """Attach the handlers, once per process. Idempotent — every module calls get_logger."""
    global _ready
    if _ready:
        return
    log = logging.getLogger(ROOT)
    log.setLevel(logging.DEBUG)          # the HANDLERS filter; the logger must pass everything
    log.propagate = False                # never reach an importer's root handlers
    level = os.environ.get("MELB_LOG_LEVEL", "INFO").upper()
    style = os.environ.get("MELB_LOG_STYLE", "plain").lower()

    con = logging.StreamHandler(stream=sys.stdout)
    con.setLevel(getattr(logging, level, logging.INFO))
    con.setFormatter(logging.Formatter(STAMPED if style == "stamped" else PLAIN,
                                       datefmt="%H:%M:%S"))
    log.addHandler(con)

    dest = os.environ.get("MELB_LOG_FILE", "").strip()
    if dest:
        # append, because the seven steps are seven processes and each opens this file in turn;
        # mode="w" would leave only s3c's share of the build in it.
        fh = logging.FileHandler(Path(dest).expanduser(), mode="a", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(STAMPED, datefmt="%Y-%m-%d %H:%M:%S"))
        log.addHandler(fh)
    _ready = True


def get_logger(who=__name__):
    """The logger for one pipeline step. Pass `__file__` and the step prefix becomes its name."""
    _configure()
    return logging.getLogger(f"{ROOT}.{_step_name(who)}")


# ── THE REPORT PRIMITIVES ─────────────────────────────────────────────────────────────
# A phase report is a handful of facts, and these four put them on the page the same way every
# time. They take the logger rather than reaching for one, so a report function can be pointed at
# a different logger (a test, a notebook) without touching anything global.

WIDTH = 92          # the runner's banner width — everything lines up inside it


def banner(log, title, sub=""):
    """A phase's section head. Wide rule, the title, and one optional line saying what it is."""
    log.info("")
    log.info("  " + "─" * (WIDTH - 2))
    log.info(f"  {title.upper()}" + (f"   ·   {sub}" if sub else ""))
    log.info("  " + "─" * (WIDTH - 2))


def kv(log, label, value, note="", pad=34, indent="    "):
    """One fact: a label, its value, and why it matters. The value is right-aligned on `pad`."""
    val = f"{value:,}" if isinstance(value, int) else str(value)
    log.info(f"{indent}{label:<{pad}}{val:>12}" + (f"   {note}" if note else ""))


def table(log, headers, rows, widths=None, indent="    ", align=None):
    """A plain column table. First column left, the rest right, unless `align` says otherwise.

    `align` is one character per column — "l" or "r". It exists for the trailing column that
    holds a phrase rather than a number: right-aligning free text inside a fixed width either
    overflows into the column beside it or pads a sentence out to the margin, and both read as
    a formatting bug. A column that is `l` is left-aligned and never truncated.
    """
    widths = widths or [max([len(str(headers[i]))] + [len(str(r[i])) for r in rows])
                        for i in range(len(headers))]
    align = align or ("l" + "r" * (len(headers) - 1))

    def line(cells):
        # the gap is BETWEEN cells, not inside a width: a right-aligned number column that
        # carries its own padding runs straight into whatever is left-aligned beside it, and
        # "125,278a ceiling on a lane" is how that reads.
        return (indent + "  ".join(f"{str(c):<{w}}" if a == "l" else f"{str(c):>{w}}"
                                   for c, w, a in zip(cells, widths, align))).rstrip()

    log.info(line(headers))
    for r in rows:
        log.info(line(r))


def dist(log, pairs, total=None, *, head="bucket", unit="articles", indent="    ",
         note=None, tail=None):
    """The distribution table the scan Sankeys print: volume, share, running cumulative.

    `pairs` is [(label, volume)] IN THE ORDER TO PRINT — the cumulative column only means
    something if the caller has sorted it, and only the caller knows whether that is by bucket
    (a touch count) or by size (a site list).

    Two ways to say more about a row, and they are not interchangeable. `tail` is a dict of
    label -> one short phrase, printed to the RIGHT of the row, for when the bucket needs
    naming ("interstate — lodged in another state"). `note` is called with the label and
    returns whole lines printed UNDER it, for when the bucket is more than one population and
    the parts need their own volumes and shares.
    """
    rows = list(pairs)
    total = total if total is not None else sum(v for _, v in rows)
    total = total or 1
    tail = tail or {}
    log.info(f"{indent}{head:<14}{unit:>12}{'share':>9}{'cumulative':>12}")
    run = 0
    for lab, v in rows:
        run += v
        log.info(f"{indent}{str(lab):<14}{v:>12,}{100 * v / total:>8.1f}%"
                 f"{100 * run / total:>11.1f}%"
                 + (f"   {tail[lab]}" if lab in tail else ""))
        for extra in (note(lab) if note else []) or []:
            log.info(f"{indent}{'':<14}{extra}")


def wrap(log, text, indent="    ", width=WIDTH):
    """One long sentence over as many lines as it needs. Prose only — never a formatted row."""
    line = indent
    for word in text.split():
        if len(line) + len(word) + 1 > width and line.strip():
            log.info(line.rstrip())
            line = indent + "  "          # continuations hang, so the sentence reads as one
        line += word + " "
    if line.strip():
        log.info(line.rstrip())


def pct(v, total):
    """`12.4%`, and `—` rather than a ZeroDivisionError on an empty population."""
    return f"{100 * v / total:.1f}%" if total else "—"


if __name__ == "__main__":
    log = get_logger("demo")
    banner(log, "the primitives", "what a phase report is built from")
    kv(log, "articles in the reduction", 166159, "every delivery date in the extract")
    dist(log, [("1", 96274), ("2", 43112), ("3+", 9873)], head="buildings")
