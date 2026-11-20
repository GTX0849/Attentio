"""Tune mode's labels: the number keys and the holder that carries the label between threads."""

from worker.concurrency import LabelHolder
from worker.tuning import LABEL_NAMES, NO_LABEL, handle_tune_key, label_for_key


def test_the_label_holder_starts_at_nothing_and_keeps_what_it_is_given() -> None:
    holder = LabelHolder()
    assert holder.get() == NO_LABEL == 0
    holder.set(3)
    assert holder.get() == 3


def test_each_number_key_maps_to_its_label() -> None:
    assert [label_for_key(ord(c)) for c in "012345"] == [0, 1, 2, 3, 4, 5]


def test_other_keys_are_not_labels() -> None:
    for key in (ord("6"), ord("9"), ord("q"), ord("c"), ord("m"), ord("a"), 255, -1, 0):
        assert label_for_key(key) is None


def test_the_label_names_follow_the_ticket() -> None:
    assert LABEL_NAMES == {
        0: "No label",
        1: "Watching the teacher",
        2: "Taking notes",
        3: "Phone in lap",
        4: "Talking to a neighbour",
        5: "Eyes closed",
    }


def test_a_label_key_sets_the_label_and_other_keys_leave_it() -> None:
    holder = LabelHolder()
    assert handle_tune_key(ord("3"), holder) is True
    assert holder.get() == 3
    assert handle_tune_key(ord("q"), holder) is False
    assert handle_tune_key(ord("9"), holder) is False
    assert holder.get() == 3
    assert handle_tune_key(ord("0"), holder) is True
    assert holder.get() == 0


def test_without_tune_mode_number_keys_do_nothing() -> None:
    assert handle_tune_key(ord("3"), None) is False
