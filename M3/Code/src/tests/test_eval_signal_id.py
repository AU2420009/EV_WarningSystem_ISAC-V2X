import random

import pytest

from isacv2x.common import packets as P
from isacv2x.common.config import load_config
from isacv2x.eval.signal_id import (evaluate_signal_id, flip_bits, full_detector,
                                    header_detector, make_corpus, score)

cfg = load_config("config/system.yaml")
PRE = cfg.alert.preamble


def alert_frame(seq=1):
    return P.encode(P.Alert(1, 7, seq, ("S12",)), PRE)


def test_clean_corpus_is_perfect_for_full_decoder():
    r = evaluate_signal_id(cfg, n_alerts=300, n_negatives=300, ber=0.0)
    f = r["full"]
    assert f.false_negatives == 0 and f.false_positives == 0 and f.meets_target
    assert f.positives == 300 and f.negatives == 300


def test_header_detector_accepts_corrupt_body_but_full_rejects_it():
    data = bytearray(alert_frame())
    data[-6] ^= 0xFF                                   # damage payload, header intact
    assert header_detector(PRE)(bytes(data)) is True
    assert full_detector(PRE)(bytes(data)) is False


def test_header_detector_has_false_positives_the_full_one_avoids():
    beacon = bytearray(P.encode(P.Beacon(5, 1, "S12", 10.0, 3.0), PRE))
    beacon[len(PRE) + 1] = P.TYPE_ALERT                # beacon whose type byte became "alert"
    assert header_detector(PRE)(bytes(beacon)) is True
    assert full_detector(PRE)(bytes(beacon)) is False


def test_noise_leads_to_fn_proportional_to_ber():
    low = evaluate_signal_id(cfg, n_alerts=1000, n_negatives=1000, ber=1e-4, seed=3)["full"]
    high = evaluate_signal_id(cfg, n_alerts=1000, n_negatives=1000, ber=5e-3, seed=3)["full"]
    assert low.fn_rate < high.fn_rate
    assert high.fn_rate > 0.5 and not high.meets_target
    assert low.false_positives == 0                    # CRC32 keeps non-EV frames out


def test_corpus_is_reproducible_and_labelled():
    a = make_corpus(cfg, n_alerts=20, n_negatives=20, seed=9)
    b = make_corpus(cfg, n_alerts=20, n_negatives=20, seed=9)
    assert a == b and sum(lab for _, lab in a) == 20 and len(a) == 40


def test_score_edge_cases():
    r = score([], full_detector(PRE), "full")
    assert not r.meets_target and r.positives == 0
    with pytest.raises(ValueError):
        make_corpus(cfg, ber=2.0)


def test_flip_bits_changes_roughly_expected_fraction():
    rng = random.Random(0)
    data = bytes(1000)
    flipped = flip_bits(data, 0.01, rng)
    ones = sum(bin(b).count("1") for b in flipped)
    assert 40 < ones < 130                             # ~80 expected of 8000 bits
    assert flip_bits(data, 0.0, rng) == data
