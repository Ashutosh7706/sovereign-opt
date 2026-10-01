"""Fuzzing the MPS parser (audit #67). No extra dependency: seeded random mutations of a valid
file. Invariant: the parser either returns a Model or raises MPSError - never another
exception, never a hang."""
import random
import time

import pytest

from sovereign.model import Model
from sovereign.mps import MPSError, parse_mps
from test_audit_replay_mps import MPS

TOKENS = ["ROWS", "COLUMNS", "RHS", "RANGES", "BOUNDS", "ENDATA", "MARKER", "'INTORG'", "'INTEND'", "N", "L", "G",
          "E", "UP", "LO", "FX", "FR", "MI", "PL", "BV", "LI", "UI", "nan", "inf", "-inf", "1e308", "-0", "x",
          "OBJSENSE", "MAX", "\t", "", "1e-400", "999999999999999999999"]


def mutate(text: str, rng: random.Random) -> str:
    lines = text.splitlines()
    for _ in range(rng.randint(1, 6)):
        op = rng.randrange(7)
        i = rng.randrange(len(lines)) if lines else 0
        if op == 0 and lines:
            del lines[i]
        elif op == 1:
            lines.insert(i, " " + " ".join(rng.choice(TOKENS) for _ in range(rng.randint(1, 5))))
        elif op == 2 and lines:
            toks = lines[i].split()
            if toks:
                toks[rng.randrange(len(toks))] = rng.choice(TOKENS)
                lines[i] = (" " if lines[i][:1].isspace() else "") + " ".join(toks)
        elif op == 3 and lines:
            j = rng.randrange(len(lines))
            lines[i], lines[j] = lines[j], lines[i]
        elif op == 4:
            lines = lines[: rng.randrange(len(lines) + 1)]
        elif op == 5 and lines:
            s = lines[i]
            k = rng.randrange(len(s) + 1)
            lines[i] = s[:k] + chr(rng.randrange(32, 0x2FFF)) + s[k:]
        elif op == 6 and lines:
            lines[i] = lines[i] * rng.randint(2, 4)
    return "\n".join(lines)


@pytest.mark.parametrize("seed", range(600))
def test_mutated_mps_never_crashes(seed):
    rng = random.Random(seed)
    text = mutate(MPS, rng)
    t = time.perf_counter()
    try:
        m = parse_mps(text)
        assert isinstance(m, Model)
    except MPSError:
        pass
    assert time.perf_counter() - t < 2.0


def test_random_bytes_never_crash():
    rng = random.Random(1)
    for _ in range(300):
        blob = "".join(chr(rng.randrange(1, 0x7F)) for _ in range(rng.randrange(0, 400)))
        try:
            parse_mps(blob)
        except MPSError:
            pass


def test_size_and_time_limits():
    with pytest.raises(MPSError, match="larger"):
        parse_mps("x" * 2000, max_bytes=1000)
    huge = "ROWS\n N obj\n" + "".join(f" L r{i}\n" for i in range(200_000))
    with pytest.raises(MPSError, match="time limit"):
        parse_mps(huge, time_limit=0.0)
